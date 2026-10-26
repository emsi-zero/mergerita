from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from audit.services import record
from integrations import outbox
from reviews.alerts import queue_admin_alert, queue_notification
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    Leave,
    OwnerRole,
    Ownership,
    Person,
)

BOOKING_LOCK_KEY = 7_340_001

TIER_RANK = {OwnerRole.PRIMARY: 0, OwnerRole.BACKUP: 1}


@dataclass(frozen=True)
class _Candidate:
    person: Person
    tier: OwnerRole
    open_bookings: int


def lock_booking() -> None:
    """Serialize bookings with a transaction-scoped advisory lock.

    Call it once, before any CapacityDay lock, inside the caller's atomic
    block. It prevents lock cycles between bookings that lock rows in
    different orders. The row locks stay: they enforce INV-3.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BOOKING_LOCK_KEY])


def book_area_review(area_review: AreaReview) -> bool:
    """Book a reviewer for an unbooked area review. Return True when booked.

    Pick the earliest day in the horizon on which a code owner has a free slot,
    then rank the owners of that day. The caller owns the transaction and has
    no adapter call open (INV-4, INV-5). The CapacityDay rows of the candidates
    stay locked until the caller commits (INV-3).
    """
    merge_request = area_review.merge_request
    areas = list(area_review.areas.all())
    candidates = _find_candidates(areas, merge_request.author_id)
    today = timezone.localdate()
    days = [today + timedelta(days=n) for n in range(settings.RMO_BOOKING_HORIZON_DAYS)]
    capacity = _lock_capacity(candidates, days)
    for day in days:
        fits = []
        for candidate in candidates:
            row = capacity.get((candidate.person.pk, day))
            if row is None:
                continue
            free = _budget(candidate.person) - row.booked_minutes
            if free >= settings.RMO_SLOT_MINUTES:
                fits.append(
                    (
                        (
                            TIER_RANK[candidate.tier],
                            -free,
                            candidate.open_bookings,
                            candidate.person.pk,
                        ),
                        candidate,
                        row,
                        free,
                    )
                )
        if fits:
            _, candidate, row, free = min(fits, key=lambda fit: fit[0])
            _book(area_review, candidate, row, free)
            return True

    queue_admin_alert(
        f"MR {merge_request.project_id}:{merge_request.iid}: no reviewer fits "
        f"within {settings.RMO_BOOKING_HORIZON_DAYS} days for area review {area_review.pk}."
    )
    return False


def _budget(person: Person) -> int:
    if person.daily_budget_minutes is not None:
        return person.daily_budget_minutes
    return settings.RMO_DEFAULT_DAILY_BUDGET_MINUTES


def _find_candidates(areas: list[CodeArea], author_id: int) -> list[_Candidate]:
    """Return owners of every area, except the author (INV-2), with their tier."""
    ownerships = (
        Ownership.objects.filter(area__in=areas)
        .exclude(person_id=author_id)
        .select_related("person")
        .order_by("person_id")
    )
    roles_by_person: dict[int, list[Ownership]] = defaultdict(list)
    for ownership in ownerships:
        roles_by_person[ownership.person_id].append(ownership)
    owners = [rows for rows in roles_by_person.values() if len(rows) == len(areas)]
    open_counts = dict(
        AreaReview.objects.filter(
            reviewer_id__in=[rows[0].person_id for rows in owners],
            state=AreaReviewState.BOOKED,
        )
        .values_list("reviewer_id")
        .annotate(total=Count("id"))
    )
    return [
        _Candidate(
            person=rows[0].person,
            tier=(
                OwnerRole.PRIMARY
                if all(row.role == OwnerRole.PRIMARY for row in rows)
                else OwnerRole.BACKUP
            ),
            open_bookings=open_counts.get(rows[0].person_id, 0),
        )
        for rows in owners
    ]


def _lock_capacity(
    candidates: list[_Candidate], days: list[date]
) -> dict[tuple[int, date], CapacityDay]:
    """Lock the capacity rows of the days each candidate can work, creating missing rows.

    Rows are locked in one query, ordered by person and day. Several calls in
    one transaction can still lock in different orders, so the caller takes
    `lock_booking()` first.
    """
    leaves = Leave.objects.filter(
        person_id__in=[candidate.person.pk for candidate in candidates],
        start_date__lte=days[-1],
        end_date__gte=days[0],
    )
    leave_ranges = defaultdict(list)
    for leave in leaves:
        leave_ranges[leave.person_id].append((leave.start_date, leave.end_date))
    working = [
        (candidate.person, day)
        for candidate in candidates
        for day in days
        if day.weekday() in candidate.person.working_days
        and not any(
            start <= day <= end for start, end in leave_ranges[candidate.person.pk]
        )
    ]
    CapacityDay.objects.bulk_create(
        [
            CapacityDay(person=person, day=day, budget_minutes=_budget(person))
            for person, day in working
        ],
        ignore_conflicts=True,
    )
    wanted = {(person.pk, day) for person, day in working}
    rows = (
        CapacityDay.objects.select_for_update(of=("self",))
        .filter(
            person_id__in=[person.pk for person, _ in working],
            day__in={day for _, day in working},
        )
        .order_by("person_id", "day")
    )
    return {
        (row.person_id, row.day): row
        for row in rows
        if (row.person_id, row.day) in wanted
    }


def _book(
    area_review: AreaReview, candidate: _Candidate, row: CapacityDay, free: int
) -> None:
    merge_request = area_review.merge_request
    if candidate.person.pk == merge_request.author_id:
        raise ValueError("The author cannot review their own merge request (INV-2).")
    before = {"state": area_review.state, "reviewer": None, "booked_day": None}
    row.booked_minutes += settings.RMO_SLOT_MINUTES
    row.budget_minutes = _budget(candidate.person)
    row.save(update_fields=["booked_minutes", "budget_minutes"])
    area_review.state = AreaReviewState.BOOKED
    area_review.reviewer = candidate.person
    area_review.booked_day = row.day
    area_review.slot_minutes = settings.RMO_SLOT_MINUTES
    area_review.reason = (
        f"{candidate.tier} owner; {row.day.isoformat()}; "
        f"{free} of {row.budget_minutes} minutes free"
    )
    area_review.save(
        update_fields=["state", "reviewer", "booked_day", "slot_minutes", "reason"]
    )
    record(
        "orchestrator",
        "area_review.booked",
        area_review,
        before,
        {
            "state": area_review.state,
            "reviewer": candidate.person.pk,
            "booked_day": area_review.booked_day.isoformat(),
            "reason": area_review.reason,
        },
    )
    outbox.enqueue(
        "set_reviewer",
        {
            "project_id": merge_request.project_id,
            "iid": merge_request.iid,
            "reviewer_gitlab_user_id": candidate.person.gitlab_user_id,
        },
    )
    queue_notification(
        [candidate.person.gitlab_user_id],
        f"You are booked to review MR {merge_request.project_id}:{merge_request.iid} "
        f"on {row.day.isoformat()}.",
    )
