"""Confirm a reported merge in GitLab, then record it (MRG-5, INV-3, INV-4)."""

from django.db import transaction
from django.utils import timezone

from integrations.adapters import get_gitlab_gateway
from reviews import coverage, merges
from reviews.ai_gate import APPROVED_STATE
from reviews.models import AreaReviewState, MergeRequest, MergeRequestState


def confirm_merge(payload: dict) -> None:
    """Outbox handler: record a merge only if GitLab shows the MR merged.

    GitLab is read first, with no transaction open (INV-4): the MR, and the
    verdict of the reviewer of each approved area review. Then the MR is
    locked and the merge recorded. A hook that GitLab does not confirm changes
    nothing, and a merge already recorded is not recorded again (INV-5).
    """
    merge_request = MergeRequest.objects.get(pk=payload["merge_request_id"])
    if merge_request.state == MergeRequestState.MERGED:
        return
    gateway = get_gitlab_gateway()
    status = gateway.get_merge_request(merge_request.project_id, merge_request.iid)
    if status.state != "merged":
        return
    verdicts = coverage.read_verdicts(
        gateway, merge_request, [AreaReviewState.APPROVED]
    )
    fresh_approver_ids = {
        verdict.reviewer_id
        for verdict in verdicts.values()
        if coverage.is_fresh(merge_request, verdict, APPROVED_STATE)
    }
    with transaction.atomic():
        locked = MergeRequest.objects.select_for_update().get(pk=merge_request.pk)
        merges.record_merge(
            locked,
            status.merged_by_id,
            status.merged_at or timezone.now(),
            fresh_approver_ids,
        )
