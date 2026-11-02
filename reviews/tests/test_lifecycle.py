from unittest import mock

from django.test import TestCase

from integrations.adapters import Changes
from integrations.fakes import FakeGitLabGateway
from integrations.models import OutboxCall
from reviews import area_resolver
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Person,
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
        warning = OutboxCall.objects.get(kind="notify", payload__gitlab_user_ids=[11])
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
