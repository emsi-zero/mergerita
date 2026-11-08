import fnmatch

from django.conf import settings
from django.db import transaction
from django.db.models import Count

from audit.services import record
from integrations import outbox
from integrations.adapters import Changes, get_gitlab_gateway
from reviews import booking, coverage
from reviews.ai_gate import APPROVED_STATE
from reviews.alerts import queue_admin_alert, queue_notification
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Ownership,
)


def resolve_areas(payload: dict) -> None:
    """Outbox handler: create the area reviews of an MR, or follow them up.

    Fetch the changes (a new MR) or the approved reviewers' verdicts (an MR that
    already has area reviews), with no transaction open. Then lock the MR and
    do all writes in one atomic block.
    """
    merge_request_id = payload["merge_request_id"]
    target = MergeRequest.objects.get(pk=merge_request_id)
    has_reviews = target.area_reviews.exists()
    verdicts: dict[int, coverage.Verdict] = {}
    changes = None
    if has_reviews:
        verdicts = coverage.read_verdicts(
            get_gitlab_gateway(), target, [AreaReviewState.APPROVED]
        )
    else:
        changes = get_gitlab_gateway().get_changes(target.project_id, target.iid)
    with transaction.atomic():
        merge_request = (
            MergeRequest.objects.select_for_update(of=("self",))
            .select_related("author")
            .get(pk=merge_request_id)
        )
        if (
            merge_request.state != MergeRequestState.AWAITING_BOOKING
            or merge_request.area_reviews.exists() != has_reviews
        ):
            return
        booking.lock_booking()
        if has_reviews:
            _follow_up(merge_request, verdicts)
        else:
            _resolve(merge_request, changes)


def mark_in_review(merge_request: MergeRequest) -> None:
    """Move an MR from `awaiting_booking` to `in_review` and audit it.

    The caller holds the MR lock and has checked the state.
    """
    merge_request.state = MergeRequestState.IN_REVIEW
    merge_request.save(update_fields=["state"])
    record(
        "orchestrator",
        "mr.in_review",
        merge_request,
        {"state": MergeRequestState.AWAITING_BOOKING},
        {"state": merge_request.state},
    )


def _follow_up(
    merge_request: MergeRequest, verdicts: dict[int, coverage.Verdict]
) -> None:
    """Book a follow-up session for each area review that is not approved (RVO-8).

    An approved area review without a fresh `approved` from its reviewer loses
    its mark first. The follow-up is `booking.book_follow_up`. No area review
    is created. The MR moves to review even when a follow-up found no fit: that
    area review is unbooked, an admin alert is queued, and it blocks approval.
    A coverage check is queued at once.
    """
    area_reviews = list(
        AreaReview.objects.select_for_update(of=("self",))
        .select_related("merge_request", "reviewer")
        .filter(merge_request=merge_request)
        .order_by("pk")
    )
    # The author pushed: every open change request is closed.
    merge_request.area_reviews.update(changes_requested_at=None)
    for area_review in area_reviews:
        if area_review.state == AreaReviewState.APPROVED:
            if coverage.is_fresh(
                merge_request, verdicts.get(area_review.pk), APPROVED_STATE
            ):
                continue
            coverage.reset_approval(area_review)
        booking.book_follow_up(area_review)
    mark_in_review(merge_request)
    outbox.enqueue("check_coverage", {"merge_request_id": merge_request.pk})


def _resolve(merge_request: MergeRequest, changes: Changes) -> None:
    areas = list(CodeArea.objects.all())
    touched: dict[int, CodeArea] = {}
    unmatched: list[str] = []
    for path in changes.files:
        matching = [
            area
            for area in areas
            if any(fnmatch.fnmatch(path, pattern) for pattern in area.path_patterns)
        ]
        if not matching:
            unmatched.append(path)
        touched.update({area.pk: area for area in matching})

    merge_request.changed_lines = changes.changed_lines
    if unmatched or not touched:
        _move_to_triage(merge_request, unmatched)
        return
    merge_request.save(update_fields=["changed_lines"])

    if changes.too_large or any(
        changes.changed_lines > area.size_limit_lines for area in touched.values()
    ):
        queue_notification(
            [merge_request.author.gitlab_user_id],
            f"MR {merge_request.project_id}:{merge_request.iid} has "
            f"{changes.changed_lines} changed lines, over the size limit of an area it touches.",
        )
    booked_all = True
    for group in _group_areas(list(touched.values()), merge_request.author_id):
        area_review = AreaReview.objects.create(merge_request=merge_request)
        area_review.areas.set(group)
        record(
            "orchestrator",
            "area_review.created",
            area_review,
            None,
            {"state": area_review.state, "areas": [area.pk for area in group]},
        )
        booked_all = booking.book_area_review(area_review) and booked_all
    if booked_all:
        mark_in_review(merge_request)


def _group_areas(touched: list[CodeArea], author_id: int) -> list[list[CodeArea]]:
    """Return the areas of each review: one group for `combined`, else one per area."""
    if settings.RMO_COVERAGE_POLICY == "combined" and touched:
        owns_all = (
            Ownership.objects.filter(area__in=touched)
            .exclude(person_id=author_id)
            .values("person")
            .annotate(owned=Count("area", distinct=True))
            .filter(owned=len(touched))
            .exists()
        )
        if owns_all:
            return [touched]
    return [[area] for area in touched]


def _move_to_triage(merge_request: MergeRequest, unmatched: list[str]) -> None:
    before = merge_request.state
    merge_request.state = MergeRequestState.NEEDS_TRIAGE
    merge_request.save(update_fields=["state", "changed_lines"])
    record(
        "orchestrator",
        "mr.needs_triage",
        merge_request,
        {"state": before},
        {"state": merge_request.state},
    )
    reason = (
        f"no code area for {', '.join(unmatched)}" if unmatched else "no changed files"
    )
    queue_admin_alert(
        f"MR {merge_request.project_id}:{merge_request.iid} needs triage: {reason}."
    )
