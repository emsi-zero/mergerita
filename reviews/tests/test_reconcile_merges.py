from datetime import UTC, datetime
from unittest import mock

from django.test import TestCase, override_settings

from integrations.adapters import MergeRequestStatus
from integrations.models import InboxEvent, OutboxCall, OutboxStatus
from reviews import merge_confirmation
from reviews.lifecycle import handle_event
from reviews.models import MergeRequest, MergeRequestState, Person
from reviews.reconciler import reconcile_all
from reviews.tests.test_reconciler import use_fake_gateway


@override_settings(RMO_GITLAB_PROJECT_IDS=[7])
class ReconcileMergesTest(TestCase):
    """Reconciliation of merges whose webhook was lost."""

    def test_mrg_9_reconciliation_records_a_merge_of_an_approved_mr(self) -> None:
        """MRG-9: Given an approved MR that GitLab shows merged, an MR in
        review that GitLab shows merged, and an approved MR whose read fails,
        when reconciliation runs, then one merge event is stored for the
        first MR, the failing read does not stop it, and handling the event
        and its job records the merge."""
        # given
        gateway = use_fake_gateway(self)
        self.enterContext(
            mock.patch.object(
                merge_confirmation, "get_gitlab_gateway", return_value=gateway
            )
        )
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        approved, in_review, failing = (
            MergeRequest.objects.create(
                project_id=7, iid=iid, author=author, title=f"MR {iid}", state=state
            )
            for iid, state in (
                (1, MergeRequestState.APPROVED),
                (2, MergeRequestState.IN_REVIEW),
                (3, MergeRequestState.APPROVED),
            )
        )
        merged = MergeRequestStatus(
            "merged", 14, datetime(2026, 11, 4, 6, 40, tzinfo=UTC)
        )
        gateway.merge_request_statuses[(7, 1)] = merged
        gateway.merge_request_statuses[(7, 2)] = merged
        statuses = gateway.merge_request_statuses

        def get_merge_request(project_id: int, iid: int) -> MergeRequestStatus:
            if iid == failing.iid:
                raise ValueError("bad body")
            return statuses[(project_id, iid)]

        gateway.get_merge_request = get_merge_request  # type: ignore[method-assign]

        # when
        reconcile_all()
        handle_event(InboxEvent.objects.get())
        merge_confirmation.confirm_merge(
            OutboxCall.objects.get(kind="confirm_merge").payload
        )

        # then
        approved.refresh_from_db()
        in_review.refresh_from_db()
        failing.refresh_from_db()
        self.assertEqual(approved.state, MergeRequestState.MERGED)
        self.assertEqual(in_review.state, MergeRequestState.IN_REVIEW)
        self.assertEqual(failing.state, MergeRequestState.APPROVED)
        self.assertEqual(approved.merge_assignments.get().merged_by_gitlab_id, 14)

        with self.subTest("a failed job is queued again"):
            # given
            gateway.get_merge_request = lambda *_: merged  # type: ignore[method-assign]
            MergeRequest.objects.filter(pk=approved.pk).update(
                state=MergeRequestState.APPROVED
            )
            OutboxCall.objects.filter(kind="confirm_merge").update(
                status=OutboxStatus.FAILED
            )

            # when
            reconcile_all()

            # then
            self.assertEqual(
                OutboxCall.objects.filter(
                    kind="confirm_merge",
                    payload={"merge_request_id": approved.pk},
                    status=OutboxStatus.PENDING,
                ).count(),
                1,
            )
