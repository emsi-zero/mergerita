"""Record a merge that GitLab reports, and check it against the rules (MRG-5..8)."""

from datetime import datetime, timedelta

from audit.services import record
from reviews.alerts import queue_admin_alert
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    MergeRoutingSettings,
    MergeViolation,
    Person,
)


def record_merge(
    merge_request: MergeRequest,
    merged_by_gitlab_id: int | None,
    merged_at: datetime,
    fresh_approver_ids: set[int],
) -> MergeAssignment | None:
    """Record who merged the MR and when, check the merge, and move the MR to `merged`.

    `fresh_approver_ids` holds the Person ids whose approval GitLab still shows
    as fresh. The merge is checked in this order: the merger is the author, the
    merger is an approver (a current reviewer of an approved area review), the
    MR was not `approved` or an approved area review has no fresh approval left.
    A violation queues an admin alert. A merge with no known merger queues an
    admin alert too. A merge outside the assigned window, or by someone other
    than the assigned merge owner, is recorded and does not alert. Without an
    open assignment, a new one with no merge owner holds the merge. A merger
    unknown to the orchestrator is recorded by id only.

    The caller owns the transaction and holds the MR row lock. Return None when
    the MR is already merged (INV-5).
    """
    if merge_request.state == MergeRequestState.MERGED:
        return None
    assignment = MergeAssignment.objects.select_related("merge_owner").filter(
        merge_request=merge_request,
        merged_at__isnull=True,
        cancelled_at__isnull=True,
    ).first() or MergeAssignment(merge_request=merge_request)
    merger = Person.objects.filter(gitlab_user_id=merged_by_gitlab_id).first()
    approved_reviews = list(
        AreaReview.objects.filter(
            merge_request=merge_request, state=AreaReviewState.APPROVED
        )
    )
    was_approved = merge_request.state == MergeRequestState.APPROVED and all(
        review.reviewer_id in fresh_approver_ids for review in approved_reviews
    )
    assignment.merged_by = merger
    assignment.merged_by_gitlab_id = merged_by_gitlab_id
    assignment.merged_at = merged_at
    assignment.outside_window = _is_outside_window(assignment.window, merged_at)
    assignment.by_other_person = (
        assignment.merge_owner is not None
        and assignment.merge_owner.gitlab_user_id != merged_by_gitlab_id
    )
    assignment.violation = _violation(
        merge_request, merger, {r.reviewer_id for r in approved_reviews}, was_approved
    )
    assignment.save()
    before = merge_request.state
    merge_request.state = MergeRequestState.MERGED
    merge_request.save(update_fields=["state"])
    record(
        "gitlab",
        "mr.merged",
        merge_request,
        {"state": before},
        {
            "state": merge_request.state,
            "merged_by_gitlab_id": merged_by_gitlab_id,
            "merged_at": merged_at.isoformat(),
            "merge_owner": assignment.merge_owner_id,
            "window": assignment.window.isoformat() if assignment.window else None,
            "outside_window": assignment.outside_window,
            "by_other_person": assignment.by_other_person,
            "violation": assignment.violation,
        },
    )
    if assignment.violation != MergeViolation.NONE:
        _alert(merge_request, merger, merged_by_gitlab_id, assignment.violation)
    if merged_by_gitlab_id is None:
        queue_admin_alert(
            f"MR {merge_request.project_id}:{merge_request.iid} {merge_request.title} "
            "was merged by an unknown user; separation of duties not checked."
        )
    return assignment


def _violation(
    merge_request: MergeRequest,
    merger: Person | None,
    approver_ids: set[int | None],
    was_approved: bool,
) -> str:
    """Return the violation of a merge: author, then approver, then before approval.

    `approver_ids` are the reviewers of the approved area reviews. `was_approved`
    is False when the MR was not approved or an approval is no longer fresh.
    """
    if merger is not None and merger.pk == merge_request.author_id:
        return MergeViolation.AUTHOR
    if merger is not None and merger.pk in approver_ids:
        return MergeViolation.APPROVER
    return MergeViolation.NONE if was_approved else MergeViolation.BEFORE_APPROVAL


def _is_outside_window(window: datetime | None, merged_at: datetime) -> bool:
    """Return True when the merge was before the window or later than its length after it."""
    if window is None:
        return False
    length = timedelta(minutes=MergeRoutingSettings.load().merge_window_minutes)
    return not window <= merged_at <= window + length


def _alert(
    merge_request: MergeRequest,
    merger: Person | None,
    merged_by_gitlab_id: int | None,
    violation: str,
) -> None:
    """Queue the admin alert for a violating merge: the MR, the merger and their role."""
    roles = {
        MergeViolation.AUTHOR: "author",
        MergeViolation.APPROVER: "approver",
        MergeViolation.BEFORE_APPROVAL: "none; the MR was not approved",
    }
    who = merger.name if merger is not None else f"GitLab user {merged_by_gitlab_id}"
    queue_admin_alert(
        f"Merge violation on MR {merge_request.project_id}:{merge_request.iid} "
        f"{merge_request.title}: merged by {who}. Role: {roles[violation]}."
    )
