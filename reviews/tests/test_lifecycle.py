from unittest import mock

from django.test import TestCase

from integrations import outbox
from integrations.adapters import Changes
from integrations.fakes import FakeGitLabGateway
from integrations.models import InboxEvent, OutboxCall
from reviews import area_resolver
from reviews.lifecycle import handle_event
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Person,
)


def label_event(iid: int, labels: list[str]) -> InboxEvent:
    return InboxEvent(
        event_kind="Merge Request Hook",
        payload={
            "project": {"id": 7},
            "object_attributes": {"iid": iid, "action": "update"},
            "labels": [{"title": label} for label in labels],
        },
    )


class LifecycleTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")
        Person.objects.create(gitlab_user_id=1, name="Admin", is_admin=True)
        CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=100
        )
        self.gateway = FakeGitLabGateway()
        patcher = mock.patch.object(
            area_resolver, "get_gitlab_gateway", return_value=self.gateway
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def create_merge_request(self, iid: int, state: str) -> MergeRequest:
        return MergeRequest.objects.create(
            project_id=7, iid=iid, author=self.author, title="MR", state=state
        )

    def test_ai_review_label_moves_merge_request(self) -> None:
        """RMO-7: Given an MR awaiting AI review, when it gets the pass label,
        then it awaits booking and gets its area reviews; when it gets the fail
        label, or both labels, then it waits on the author."""
        for iid, name, labels, expected_state, expected_reviews in (
            (3, "pass", ["ai-review::passed"], MergeRequestState.AWAITING_BOOKING, 1),
            (4, "fail", ["ai-review::failed"], MergeRequestState.WAITING_ON_AUTHOR, 0),
            (
                5,
                "both",
                ["ai-review::passed", "ai-review::failed"],
                MergeRequestState.WAITING_ON_AUTHOR,
                0,
            ),
        ):
            with self.subTest(name=name):
                # given
                merge_request = self.create_merge_request(
                    iid, MergeRequestState.AWAITING_AI_REVIEW
                )
                self.gateway.changes[(7, iid)] = Changes(
                    files=["api/a.py"], changed_lines=10
                )

                # when
                handle_event(label_event(iid, labels))
                outbox.dispatch_due()

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, expected_state)
                self.assertEqual(
                    AreaReview.objects.filter(merge_request=merge_request).count(),
                    expected_reviews,
                )

    def test_oversized_merge_request_queues_size_warning(self) -> None:
        """RMO-8: Given an MR with more changed lines than the limit of an area
        it touches, when areas are resolved, then one size warning to the author
        is in the outbox."""
        # given
        merge_request = self.create_merge_request(3, MergeRequestState.AWAITING_BOOKING)
        self.gateway.changes[(7, 3)] = Changes(files=["api/a.py"], changed_lines=101)

        # when
        area_resolver.resolve_areas({"merge_request_id": merge_request.pk})

        # then
        warning = OutboxCall.objects.get(kind="notify")
        self.assertEqual(warning.payload["gitlab_user_ids"], [11])

    def test_unmatched_or_no_files_send_merge_request_to_triage(self) -> None:
        """RMO-11: Given an MR with a file that matches no code area, or with no
        changed files, when areas are resolved, then the MR needs triage and an
        admin alert is queued."""
        for iid, name, files, expected_text in (
            (3, "unmatched_file", ["api/a.py", "docs/b.md"], "docs/b.md"),
            (4, "no_files", [], "no changed files"),
        ):
            with self.subTest(name=name):
                # given
                merge_request = self.create_merge_request(
                    iid, MergeRequestState.AWAITING_BOOKING
                )
                self.gateway.changes[(7, iid)] = Changes(files=files, changed_lines=5)

                # when
                area_resolver.resolve_areas({"merge_request_id": merge_request.pk})

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, MergeRequestState.NEEDS_TRIAGE)
                self.assertEqual(
                    AreaReview.objects.filter(merge_request=merge_request).count(), 0
                )
                alert = OutboxCall.objects.get(
                    kind="notify", payload__text__icontains=f"7:{iid} "
                )
                self.assertEqual(alert.payload["gitlab_user_ids"], [1])
                self.assertIn(expected_text, alert.payload["text"])
