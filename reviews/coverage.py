"""Coverage check: read human reviewers' verdicts from GitLab and move the MR (RVO-7)."""

from datetime import datetime
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone

from audit.services import record
from integrations.adapters import GitLabGatewayInterface, get_gitlab_gateway
from reviews import booking, merge_router
from reviews.ai_gate import APPROVED_STATE, REQUESTED_CHANGES_STATE
from reviews.alerts import queue_notification
from reviews.lifecycle import COVERAGE_STATES
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeRequest,
    MergeRequestState,
    ReviewSessionOutcome,
)


class Verdict(NamedTuple):
    """A reviewer's latest verdict time and review state, as read from GitLab."""

    reviewer_id: int
    time: datetime | None
    state: str | None


def latest_push(merge_request: MergeRequest) -> datetime:
    """Return the time of the MR's latest push: the later of its AI-gate entry and head sha time."""
    return max(
        time
        for time in (merge_request.ai_gate_entered_at, merge_request.head_sha_at)
        if time is not None
    )


def is_fresh(merge_request: MergeRequest, verdict: Verdict | None, state: str) -> bool:
    """Return True when the verdict has this review state and is newer than the latest push."""
    return (
        verdict is not None
        and verdict.state == state
        and verdict.time is not None
        and verdict.time > latest_push(merge_request)
    )


def read_verdicts(
    gateway: GitLabGatewayInterface,
    merge_request: MergeRequest,
    area_states: list[str],
) -> dict[int, Verdict]:
    """Read, for each area review in `area_states`, its reviewer's verdict (INV-3).

    The verdict time is read first, then the state: a state newer than the
    time is still safe, an older one is ignored as not fresh. Call it with no
    transaction open.
    """
    verdicts = {}
    for area_review in merge_request.area_reviews.filter(
        state__in=area_states
    ).select_related("reviewer"):
        gitlab_id = area_review.reviewer.gitlab_user_id
        verdict_time = gateway.get_latest_verdict_time(
            merge_request.project_id, merge_request.iid, gitlab_id
        )
        verdicts[area_review.pk] = Verdict(
            area_review.reviewer_id,
            verdict_time,
            gateway.get_reviewer_state(
                merge_request.project_id, merge_request.iid, gitlab_id
            ),
        )
    return verdicts


def check_coverage(payload: dict) -> None:
    """Outbox handler: approve areas and the MR from the reviewers' fresh verdicts.

    First read the verdicts of the booked and approved area reviews, with no
    transaction open (INV-3). Then lock the MR, the booking lock and the area
    reviews, in that order (INV-6), and act only while the MR is in review,
    approved, or waiting on the author after a reviewer's change request that
    this reviewer has since approved. An approved MR whose area review lost its
    approval returns to review and its merge assignment is cancelled. A
    verdict counts when it is newer than the MR's latest push. A fresh
    `approved` approves a booked area review. An approved area review without
    a fresh `approved` goes back to booked and gets a follow-up. A fresh
    `requested_changes` returns the MR to its author. An area review whose
    reviewer changed since the read is skipped.
    """
    merge_request_id = payload["merge_request_id"]
    verdicts = read_verdicts(
        get_gitlab_gateway(),
        MergeRequest.objects.get(pk=merge_request_id),
        [AreaReviewState.BOOKED, AreaReviewState.APPROVED],
    )
    with transaction.atomic():
        merge_request = MergeRequest.objects.select_for_update(of=("self",)).get(
            pk=merge_request_id
        )
        if merge_request.state not in COVERAGE_STATES:
            return
        booking.lock_booking()
        area_reviews = list(
            AreaReview.objects.select_for_update(of=("self",))
            .select_related("reviewer", "merge_request")
            .filter(merge_request=merge_request)
            .order_by("pk")
        )
        recorded = _record_change_requests(merge_request, area_reviews, verdicts)
        if merge_request.state == MergeRequestState.WAITING_ON_AUTHOR and not (
            _resume_review(merge_request, area_reviews, verdicts)
        ):
            _notify_author(merge_request, recorded)
            return
        _apply_verdicts(merge_request, area_reviews, verdicts)
        if recorded and merge_request.state == MergeRequestState.IN_REVIEW:
            _move_to_author(merge_request, recorded[0])
        _notify_author(merge_request, recorded)


def _record_change_requests(
    merge_request: MergeRequest,
    area_reviews: list[AreaReview],
    verdicts: dict[int, Verdict],
) -> list[AreaReview]:
    """Record each new fresh change request on its area review and return those area reviews.

    An approved area review goes back to booked, with no follow-up before the
    author pushes. The request time is GitLab's own clock.
    """
    recorded = []
    for area_review in area_reviews:
        verdict = verdicts.get(area_review.pk)
        if (
            verdict is None
            or area_review.state
            not in (AreaReviewState.BOOKED, AreaReviewState.APPROVED)
            or area_review.reviewer_id != verdict.reviewer_id
            or not is_fresh(merge_request, verdict, REQUESTED_CHANGES_STATE)
            or area_review.changes_requested_at == verdict.time
        ):
            continue
        before = area_review.state
        area_review.state = AreaReviewState.BOOKED
        area_review.changes_requested_at = verdict.time
        area_review.save(update_fields=["state", "changes_requested_at"])
        record(
            "gitlab",
            "area_review.changes_requested",
            area_review,
            {"state": before},
            {
                "state": area_review.state,
                "changes_requested_at": verdict.time.isoformat(),
            },
        )
        recorded.append(area_review)
    return recorded


def _resume_review(
    merge_request: MergeRequest,
    area_reviews: list[AreaReview],
    verdicts: dict[int, Verdict],
) -> bool:
    """Move the MR back to review when every open change request is freshly approved.

    An approval counts when it is newer than the latest push and newer than
    its own change request, and every change request must be newer than the
    latest push. Return False when no change request is open, or one is not
    approved yet (RVO-7). Other area reviews stay untouched.
    """
    open_reviews = [
        area_review
        for area_review in area_reviews
        if area_review.changes_requested_at is not None
    ]
    if not open_reviews or not all(
        area_review.changes_requested_at > latest_push(merge_request)
        and is_fresh(merge_request, verdicts.get(area_review.pk), APPROVED_STATE)
        and verdicts[area_review.pk].reviewer_id == area_review.reviewer_id
        and verdicts[area_review.pk].time > area_review.changes_requested_at
        for area_review in open_reviews
    ):
        return False
    for area_review in open_reviews:
        area_review.changes_requested_at = None
        area_review.save(update_fields=["changes_requested_at"])
    merge_request.state = MergeRequestState.IN_REVIEW
    merge_request.save(update_fields=["state"])
    record(
        "gitlab",
        "mr.in_review",
        merge_request,
        {"state": MergeRequestState.WAITING_ON_AUTHOR},
        {"state": merge_request.state},
    )
    return True


def _apply_verdicts(
    merge_request: MergeRequest,
    area_reviews: list[AreaReview],
    verdicts: dict[int, Verdict],
) -> None:
    for area_review in area_reviews:
        verdict = verdicts.get(area_review.pk)
        if (
            verdict is None
            or area_review.state
            not in (AreaReviewState.BOOKED, AreaReviewState.APPROVED)
            or area_review.reviewer_id != verdict.reviewer_id
        ):
            continue
        is_approved = is_fresh(merge_request, verdict, APPROVED_STATE)
        if area_review.state == AreaReviewState.BOOKED and is_approved:
            _approve_area_review(area_review)
        elif area_review.state == AreaReviewState.APPROVED and not is_approved:
            reset_approval(area_review)
            booking.book_follow_up(area_review)
    all_approved = all(
        review.state == AreaReviewState.APPROVED for review in area_reviews
    )
    if merge_request.state == MergeRequestState.APPROVED:
        if not all_approved:
            _revoke_approval(merge_request)
    elif area_reviews and all_approved:
        _approve(merge_request)


def _revoke_approval(merge_request: MergeRequest) -> None:
    """Send an approved MR back to review and cancel its merge assignment (MRG-11)."""
    merge_request.state = MergeRequestState.IN_REVIEW
    merge_request.save(update_fields=["state"])
    record(
        "gitlab",
        "mr.in_review",
        merge_request,
        {"state": MergeRequestState.APPROVED},
        {"state": merge_request.state},
    )
    merge_router.cancel_open_assignment(merge_request, "an approval was revoked")


def reset_approval(area_review: AreaReview) -> None:
    """Take the approved mark off an area review: GitLab no longer shows it."""
    area_review.state = AreaReviewState.BOOKED
    area_review.save(update_fields=["state"])
    record(
        "gitlab",
        "area_review.approval_reset",
        area_review,
        {"state": AreaReviewState.APPROVED},
        {"state": area_review.state},
    )


def _approve_area_review(area_review: AreaReview) -> None:
    """Mark the area review approved and close its pending session as done."""
    area_review.state = AreaReviewState.APPROVED
    area_review.save(update_fields=["state"])
    record(
        "gitlab",
        "area_review.approved",
        area_review,
        {"state": AreaReviewState.BOOKED},
        {"state": area_review.state},
    )
    session = area_review.sessions.filter(outcome=ReviewSessionOutcome.PENDING).first()
    if session is None:
        return
    session.outcome = ReviewSessionOutcome.DONE
    session.outcome_at = timezone.now()
    session.save(update_fields=["outcome", "outcome_at"])
    record(
        "gitlab",
        "review_session.done",
        session,
        {"outcome": ReviewSessionOutcome.PENDING},
        {"outcome": session.outcome},
    )


def _move_to_author(merge_request: MergeRequest, area_review: AreaReview) -> None:
    merge_request.state = MergeRequestState.WAITING_ON_AUTHOR
    merge_request.save(update_fields=["state"])
    record(
        "gitlab",
        "mr.changes_requested",
        merge_request,
        {"state": MergeRequestState.IN_REVIEW},
        {"state": merge_request.state, "reviewer": area_review.reviewer_id},
    )


def _notify_author(merge_request: MergeRequest, recorded: list[AreaReview]) -> None:
    for area_review in recorded:
        queue_notification(
            [merge_request.author.gitlab_user_id],
            f"{area_review.reviewer.name} requested changes on !{merge_request.iid}.",
        )


def _approve(merge_request: MergeRequest) -> None:
    merge_request.state = MergeRequestState.APPROVED
    merge_request.approved_at = timezone.now()
    merge_request.save(update_fields=["state", "approved_at"])
    record(
        "gitlab",
        "mr.approved",
        merge_request,
        {"state": MergeRequestState.IN_REVIEW},
        {"state": merge_request.state},
    )
    merge_router.assign_merge(merge_request)
