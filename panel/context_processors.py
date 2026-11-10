"""Header data for panel pages: the signed-in person, their areas and tab counts (PNS-2)."""

from datetime import timedelta

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone

from reviews import urgency
from reviews.models import (
    AreaReviewState,
    CodeArea,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
)


def panel_header(request: HttpRequest) -> dict:
    """Return `panel_person`, `panel_areas` and `tab_counts` on panel pages only.

    Every query filters on the signed-in user's Person (INV-1).
    """
    match = request.resolver_match
    if match is None or match.namespace != "panel" or not request.user.is_authenticated:
        return {}
    try:
        person = request.user.person
    except Person.DoesNotExist:
        return {}
    last_day = timezone.localdate() + timedelta(
        days=settings.RMO_BOOKING_HORIZON_DAYS - 1
    )
    counts = {
        "mrs": MergeRequest.objects.filter(author=person)
        .exclude(state=MergeRequestState.MERGED)
        .count(),
        "reviews": ReviewSession.objects.filter(
            reviewer=person,
            outcome=ReviewSessionOutcome.PENDING,
            day__lte=last_day,
            area_review__reviewer=person,
            area_review__state=AreaReviewState.BOOKED,
        ).count(),
    }
    if person.is_merge_owner:
        counts["merges"] = MergeAssignment.objects.filter(
            merge_owner=person, merged_at__isnull=True, cancelled_at__isnull=True
        ).count()
    decidable = urgency.decidable_requests(person).count()
    if person.is_admin or decidable:
        counts["urgency"] = decidable
    return {
        "panel_person": person,
        "panel_team": person.team,
        "panel_reports_to": person.reports_to,
        "panel_initials": "".join(part[0] for part in person.name.split()[:2]).upper(),
        "panel_areas": list(
            CodeArea.objects.filter(ownership__person=person)
            .distinct()
            .order_by("name")
            .values_list("name", flat=True)
        ),
        "tab_counts": counts,
    }
