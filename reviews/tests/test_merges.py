from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from audit.models import AuditEvent
from integrations.adapters import MergeRequestStatus
from integrations.fakes import FakeGitLabGateway
from integrations.models import InboxEvent, OutboxCall
from reviews import merge_confirmation
from reviews.lifecycle import handle_event
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    MergeViolation,
    Person,
)

TEHRAN = ZoneInfo("Asia/Tehran")
WINDOW = datetime(2026, 11, 4, 10, 0, tzinfo=TEHRAN)
AUTHOR_ID = 11
APPROVER_ID = 12
OWNER_ID = 13
STRANGER_ID = 14
ADMIN_ID = 1


def merge_event(action: str = "merge", source: str = "webhook") -> InboxEvent:
    """A Merge Request Hook (action `merge`) or a reconciliation event (`merged`)."""
    return InboxEvent(
        source=source,
        event_kind="Merge Request Hook",
        payload={
            "user": {"id": STRANGER_ID},
            "project": {"id": 7},
            "object_attributes": {"iid": 5, "action": action},
        },
    )


def merged(merged_by: int | None, merged_at: str) -> MergeRequestStatus:
    return MergeRequestStatus("merged", merged_by, datetime.fromisoformat(merged_at))


class MergeRecordingTest(TestCase):
    """Merges that GitLab reports, confirmed by the `confirm_merge` job."""

    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=AUTHOR_ID, name="Author")
        self.approver = Person.objects.create(gitlab_user_id=APPROVER_ID, name="Rev")
        self.owner = Person.objects.create(
            gitlab_user_id=OWNER_ID, name="Owner", is_merge_owner=True
        )
        Person.objects.create(gitlab_user_id=STRANGER_ID, name="Stranger")
        Person.objects.create(gitlab_user_id=ADMIN_ID, name="Admin", is_admin=True)
        self.gateway = FakeGitLabGateway()
        self.enterContext(
            mock.patch.object(
                merge_confirmation, "get_gitlab_gateway", return_value=self.gateway
            )
        )

    def create_merge_request(
        self, state: str, approval: str | None = "approved"
    ) -> MergeRequest:
        """An MR with one approved area review; `approval` is the reviewer's state in GitLab."""
        merge_request = MergeRequest.objects.create(
            project_id=7, iid=5, author=self.author, title="MR 5", state=state
        )
        AreaReview.objects.create(
            merge_request=merge_request,
            reviewer=self.approver,
            state=AreaReviewState.APPROVED,
        )
        self.gateway.reviewer_states[(7, 5, APPROVER_ID)] = approval
        self.gateway.latest_verdict_times[(7, 5, APPROVER_ID)] = (
            timezone.now() + timedelta(hours=1)
        )
        return merge_request

    def assign(self, merge_request: MergeRequest) -> MergeAssignment:
        return MergeAssignment.objects.create(
            merge_request=merge_request, merge_owner=self.owner, window=WINDOW
        )

    def confirm(self, merge_request: MergeRequest) -> None:
        confirm_payload = {"merge_request_id": merge_request.pk}
        merge_confirmation.confirm_merge(confirm_payload)

    def alerts(self) -> list[str]:
        return [
            call.payload["text"]
            for call in OutboxCall.objects.filter(
                kind="notify", payload__gitlab_user_ids=[ADMIN_ID]
            )
        ]

    def test_mrg_5_merge_is_recorded_only_when_gitlab_confirms_it(self) -> None:
        """MRG-5: Given an approved MR with an assignment, when a Merge Request
        Hook or a reconciliation event reports it merged and GitLab confirms
        it, then the merger and time come from GitLab, the MR is merged and an
        audit event is written, once only; and when GitLab shows it still
        open, then nothing changes."""
        at = datetime(2026, 11, 4, 10, 10, tzinfo=TEHRAN)
        for name, event, status, expected, merger, merged_at, audits in (
            (
                "hook",
                merge_event(),
                merged(OWNER_ID, "2026-11-04T06:40:00+00:00"),
                MergeRequestState.MERGED,
                OWNER_ID,
                at,
                1,
            ),
            (
                "reconcile",
                merge_event("merged", "reconcile"),
                merged(OWNER_ID, "2026-11-04T06:40:00+00:00"),
                MergeRequestState.MERGED,
                OWNER_ID,
                at,
                1,
            ),
            (
                "unconfirmed_hook",
                merge_event(),
                MergeRequestStatus("opened", None, None),
                MergeRequestState.APPROVED,
                None,
                None,
                0,
            ),
        ):
            with self.subTest(name):
                # given
                MergeRequest.objects.all().delete()
                MergeAssignment.objects.all().delete()
                merge_request = self.create_merge_request(MergeRequestState.APPROVED)
                self.assign(merge_request)
                self.gateway.merge_request_statuses[(7, 5)] = status

                # when
                handle_event(event)
                queued = OutboxCall.objects.filter(
                    kind="confirm_merge", payload={"merge_request_id": merge_request.pk}
                ).count()
                self.confirm(merge_request)
                self.confirm(merge_request)

                # then
                merge_request.refresh_from_db()
                assignment = MergeAssignment.objects.get()
                self.assertEqual(queued, 1)
                self.assertEqual(merge_request.state, expected)
                self.assertEqual(assignment.merged_by_gitlab_id, merger)
                self.assertEqual(assignment.merged_at, merged_at)
                self.assertEqual(
                    AuditEvent.objects.filter(
                        action="mr.merged", subject_id=str(merge_request.pk)
                    ).count(),
                    audits,
                )

    def test_mrg_6_merge_by_the_author_or_an_approver_alerts_the_admin(self) -> None:
        """MRG-6: Given an approved MR, when its author or an approver merges
        it, then the merge is marked as a violation and the admin is alerted
        with the MR, the merger and the role."""
        for name, merger_id, violation, role in (
            ("author", AUTHOR_ID, MergeViolation.AUTHOR, "Author"),
            ("approver", APPROVER_ID, MergeViolation.APPROVER, "Rev"),
        ):
            with self.subTest(name):
                # given
                MergeRequest.objects.all().delete()
                MergeAssignment.objects.all().delete()
                OutboxCall.objects.all().delete()
                merge_request = self.create_merge_request(MergeRequestState.APPROVED)
                self.assign(merge_request)
                self.gateway.merge_request_statuses[(7, 5)] = merged(
                    merger_id, "2026-11-04T06:40:00+00:00"
                )

                # when
                self.confirm(merge_request)

                # then
                self.assertEqual(MergeAssignment.objects.get().violation, violation)
                self.assertEqual(
                    self.alerts(),
                    [
                        f"Merge violation on MR 7:5 MR 5: merged by {role}. "
                        f"Role: {name}."
                    ],
                )

    def test_mrg_7_merge_without_a_fresh_approval_alerts_the_admin(self) -> None:
        """MRG-7: Given an MR in review, or an approved MR whose approver no
        longer has a fresh approval in GitLab, when an unrelated person merges
        it, then it is recorded as merged before approval and the admin is
        alerted."""
        for name, state, approval in (
            ("in_review", MergeRequestState.IN_REVIEW, "approved"),
            ("revoked_before_merge", MergeRequestState.APPROVED, "reviewed"),
        ):
            with self.subTest(name):
                # given
                MergeRequest.objects.all().delete()
                MergeAssignment.objects.all().delete()
                OutboxCall.objects.all().delete()
                merge_request = self.create_merge_request(state, approval)
                self.gateway.merge_request_statuses[(7, 5)] = merged(
                    STRANGER_ID, "2026-11-04T06:40:00+00:00"
                )

                # when
                self.confirm(merge_request)

                # then
                merge_request.refresh_from_db()
                assignment = MergeAssignment.objects.get()
                self.assertEqual(merge_request.state, MergeRequestState.MERGED)
                self.assertEqual(assignment.violation, MergeViolation.BEFORE_APPROVAL)
                self.assertIsNone(assignment.merge_owner)
                self.assertEqual(len(self.alerts()), 1)

    def test_mrg_8_merge_outside_the_window_or_by_another_is_recorded_quietly(
        self,
    ) -> None:
        """MRG-8: Given an approved MR assigned to a window of 30 minutes,
        when it is merged before the window, after it, inside it, or by
        another person, then the flags are recorded in the merge and its audit
        event; and when it has no merge owner, it is not marked as by another
        person; and no alert is queued."""
        for name, merger_id, merged_at, outside, other, owned in (
            ("inside", OWNER_ID, "2026-11-04T06:30:00+00:00", False, False, True),
            ("window_end", OWNER_ID, "2026-11-04T07:00:00+00:00", False, False, True),
            ("before", OWNER_ID, "2026-11-04T06:29:59+00:00", True, False, True),
            ("after", OWNER_ID, "2026-11-04T07:00:01+00:00", True, False, True),
            (
                "other_person",
                STRANGER_ID,
                "2026-11-04T06:40:00+00:00",
                False,
                True,
                True,
            ),
            ("no_owner", STRANGER_ID, "2026-11-04T06:40:00+00:00", False, False, False),
        ):
            with self.subTest(name):
                # given
                MergeRequest.objects.all().delete()
                MergeAssignment.objects.all().delete()
                OutboxCall.objects.all().delete()
                merge_request = self.create_merge_request(MergeRequestState.APPROVED)
                if owned:
                    self.assign(merge_request)
                self.gateway.merge_request_statuses[(7, 5)] = merged(
                    merger_id, merged_at
                )

                # when
                self.confirm(merge_request)

                # then
                assignment = MergeAssignment.objects.get()
                audit = AuditEvent.objects.get(
                    action="mr.merged", subject_id=str(merge_request.pk)
                ).after
                self.assertEqual(
                    (assignment.outside_window, assignment.by_other_person),
                    (outside, other),
                )
                self.assertEqual(
                    (audit["outside_window"], audit["by_other_person"]),
                    (outside, other),
                )
                self.assertEqual(assignment.violation, MergeViolation.NONE)
                self.assertEqual(self.alerts(), [])

    def test_mrg_12_merge_with_no_merger_is_recorded_and_alerts_the_admin(
        self,
    ) -> None:
        """MRG-12: Given an approved MR that GitLab shows merged with neither
        `merge_user` nor `merged_by`, when the merge is confirmed, then the
        merge is recorded with an unknown merger and the admin is alerted that
        separation of duties was not checked."""
        # given
        merge_request = self.create_merge_request(MergeRequestState.APPROVED)
        self.gateway.merge_request_statuses[(7, 5)] = merged(
            None, "2026-11-04T06:40:00+00:00"
        )

        # when
        self.confirm(merge_request)

        # then
        merge_request.refresh_from_db()
        assignment = MergeAssignment.objects.get()
        self.assertEqual(merge_request.state, MergeRequestState.MERGED)
        self.assertIsNone(assignment.merged_by_gitlab_id)
        self.assertEqual(
            self.alerts(),
            [
                "MR 7:5 MR 5 was merged by an unknown user; "
                "separation of duties not checked."
            ],
        )
