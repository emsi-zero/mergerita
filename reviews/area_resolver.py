import fnmatch

from django.conf import settings
from django.db import transaction
from django.db.models import Count

from audit.services import record
from integrations.adapters import Changes, get_gitlab_gateway
from reviews import booking
from reviews.alerts import queue_admin_alert, queue_notification
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Ownership,
)


def resolve_areas(payload: dict) -> None:
    """Outbox handler: create the area reviews of an MR.

    Fetch the changes first, with no transaction open. Then lock the MR and
    do all writes in one atomic block. Return when the MR already has area reviews.
    """
    merge_request_id = payload["merge_request_id"]
    target = MergeRequest.objects.get(pk=merge_request_id)
    changes = get_gitlab_gateway().get_changes(target.project_id, target.iid)
    with transaction.atomic():
        merge_request = (
            MergeRequest.objects.select_for_update(of=("self",))
            .select_related("author")
            .get(pk=merge_request_id)
        )
        if (
            merge_request.state != MergeRequestState.AWAITING_BOOKING
            or merge_request.area_reviews.exists()
        ):
            return
        booking.lock_booking()
        _resolve(merge_request, changes)


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

    if any(changes.changed_lines > area.size_limit_lines for area in touched.values()):
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
        merge_request.state = MergeRequestState.IN_REVIEW
        merge_request.save(update_fields=["state"])
        record(
            "orchestrator",
            "mr.in_review",
            merge_request,
            {"state": MergeRequestState.AWAITING_BOOKING},
            {"state": merge_request.state},
        )


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
