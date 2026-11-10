from collections import defaultdict
from collections.abc import Collection
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
    MergeRequest,
    OwnerRole,
    Ownership,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
    UrgencyRequest,
    UrgencyRequestStatus,
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


def book_area_review(  # pylint: disable=too-many-arguments,too-many-locals
    area_review: AreaReview,
    *,
    only_reviewer: Person | None = None,
    exclude: Collection[int] = (),
    after_day: date | None = None,
    continuation: bool = False,
    alert_on_no_fit: bool = True,
    urgent: bool | None = None,
    prefer: Person | None = None,
    urgent_notes: list[str] | None = None,
) -> bool:
    """Book a session of an area review. Return True when booked.

    Pick the earliest day in the horizon on which a code owner has a free slot,
    then rank the owners of that day. `urgent` (default: the MR is urgent)
    drops only the free-slot check, so the first available owner day wins and
    may go over budget (INV-4, URG-15). An urgent booking after today, or
    with no fit, alerts the approver and the admins (URG-18), unless
    `alert_on_no_fit` is False; with `urgent_notes` the alert text is appended
    to that list for the caller to send once. `prefer` wins the earliest day
    it fits, never moving the booking to a later day (URG-15).
    `only_reviewer` narrows the candidates to one person, `exclude` holds
    person ids that must not be booked, and `after_day` skips that day and the
    days before it. The booking is a new pending session, and the area review
    caches it (INV-1). A reviewer who differs from the current one is set in
    GitLab and notified. When nothing fits, an admin alert is queued (unless
    `alert_on_no_fit` is False), and a booked area review becomes unbooked.
    The caller owns the transaction and has no adapter call open (INV-5). The
    CapacityDay rows of the candidates stay locked until the caller commits.
    """
    merge_request = area_review.merge_request
    barred = {merge_request.author_id, *exclude}
    candidates = _find_candidates(list(area_review.areas.all()), barred, only_reviewer)
    today = timezone.localdate()
    days = [today + timedelta(days=n) for n in range(settings.RMO_BOOKING_HORIZON_DAYS)]
    capacity = _lock_capacity(candidates, days)
    if urgent is None:
        urgent = merge_request.urgent_at is not None
    pick = _pick_slot(candidates, capacity, days, after_day, urgent, prefer)
    if pick is not None:
        candidate, row, free = pick
        if candidate.person.pk in barred:
            raise ValueError(
                "The author or the previous reviewer cannot be booked (INV-4)."
            )
        _book(area_review, candidate, row, free, continuation, urgent)
        if urgent and alert_on_no_fit and row.day > today:
            _urgent_alert(
                merge_request,
                f"no owner is available today; booked on {row.day}.",
                urgent_notes,
            )
        return True

    if area_review.state == AreaReviewState.BOOKED:
        _unbook(area_review)
    if urgent and alert_on_no_fit:
        _urgent_alert(
            merge_request,
            f"no owner fits within {settings.RMO_BOOKING_HORIZON_DAYS} days.",
            urgent_notes,
        )
    elif alert_on_no_fit:
        queue_admin_alert(
            f"MR {merge_request.project_id}:{merge_request.iid}: no reviewer fits "
            f"within {settings.RMO_BOOKING_HORIZON_DAYS} days for area review {area_review.pk}."
        )
    return False


def _pick_slot(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    candidates: list[_Candidate],
    capacity: dict[tuple[int, date], CapacityDay],
    days: list[date],
    after_day: date | None,
    urgent: bool,
    prefer: Person | None = None,
) -> tuple[_Candidate, CapacityDay, int] | None:
    """Return the best candidate, row and free minutes on the earliest day that fits.

    `prefer` is chosen on that day when it fits, before the normal ranking.
    """
    for day in days:
        if after_day is not None and day <= after_day:
            continue
        fits = []
        for candidate in candidates:
            row = capacity.get((candidate.person.pk, day))
            if row is None:
                continue
            free = person_budget(candidate.person) - row.booked_minutes
            if urgent or free >= settings.RMO_SLOT_MINUTES:
                rank = (
                    TIER_RANK[candidate.tier],
                    -free,
                    candidate.open_bookings,
                    candidate.person.pk,
                )
                fits.append((rank, candidate, row, free))
        if fits:
            preferred = [fit for fit in fits if fit[1].person == prefer]
            _, candidate, row, free = min(preferred or fits, key=lambda fit: fit[0])
            return candidate, row, free
    return None


def book_follow_up(area_review: AreaReview) -> bool:
    """Book a continuation session with the same reviewer and tell them. Return True when booked.

    A session of the area review that is still pending is closed as
    superseded and its minutes are released first. An unbooked area review has
    no reviewer: any eligible owner is booked. The caller owns the transaction
    and has called `lock_booking()` (INV-6).
    """
    session = area_review.sessions.filter(outcome=ReviewSessionOutcome.PENDING).first()
    if session is not None:
        supersede_session(session)
    previous_reviewer_id = area_review.reviewer_id
    # A hand back is never undone by a follow-up (RBW-5, SEP-1).
    handed_back = set(
        area_review.sessions.filter(
            outcome=ReviewSessionOutcome.HANDED_BACK
        ).values_list("reviewer_id", flat=True)
    )
    if area_review.merge_request.urgent_at is not None:
        # Urgent: the same reviewer only when they fit on the earliest day (URG-15).
        booked = book_area_review(
            area_review,
            prefer=area_review.reviewer,
            exclude=handed_back,
            continuation=True,
        )
    else:
        booked = book_area_review(
            area_review,
            only_reviewer=area_review.reviewer,
            exclude=handed_back,
            continuation=True,
        )
    if not booked:
        return False
    if area_review.reviewer_id != previous_reviewer_id:
        return True  # the booking itself told the new reviewer
    queue_notification(
        [area_review.reviewer.gitlab_user_id],
        f"Follow-up review of !{area_review.merge_request.iid} booked on "
        f"{area_review.booked_day.isoformat()}.",
    )
    return True


def _urgent_alert(
    merge_request: MergeRequest, text: str, notes: list[str] | None
) -> None:
    """Queue the URG-18 alert, or add its line to `notes` when the caller sends one alert."""
    line = f"Urgent MR {merge_request.project_id}:{merge_request.iid}: {text}"
    if notes is not None:
        notes.append(line)
    else:
        queue_urgent_alert(merge_request, line)


def queue_urgent_alert(merge_request: MergeRequest, text: str) -> None:
    """Tell the approver of the MR's approved urgency request, and the admins (URG-18)."""
    approval = (
        UrgencyRequest.objects.filter(
            merge_request=merge_request, status=UrgencyRequestStatus.APPROVED
        )
        .select_related("approver")
        .first()
    )
    ids = {person.gitlab_user_id for person in Person.objects.filter(is_admin=True)}
    if approval is not None and approval.approver is not None:
        ids.add(approval.approver.gitlab_user_id)
    queue_notification(sorted(ids), text)


def supersede_session(session: ReviewSession) -> None:
    """Close a pending session as superseded and release its minutes.

    The caller owns the transaction and has called `lock_booking()`.
    """
    session.outcome = ReviewSessionOutcome.SUPERSEDED
    session.outcome_at = timezone.now()
    session.save(update_fields=["outcome", "outcome_at"])
    release_session_minutes(session)
    record(
        "orchestrator",
        "review_session.superseded",
        session,
        {"outcome": ReviewSessionOutcome.PENDING},
        {"outcome": session.outcome},
    )


def release_session_minutes(session: ReviewSession) -> None:
    """Give a session's minutes back to its day's budget, when the day is today or later.

    The caller owns the transaction and has called `lock_booking()`. The
    CapacityDay row stays locked until the caller commits (INV-3).
    """
    if session.day < timezone.localdate():
        return
    row = (
        CapacityDay.objects.select_for_update(of=("self",))
        .filter(person_id=session.reviewer_id, day=session.day)
        .first()
    )
    if row is None:
        return
    row.booked_minutes = max(0, row.booked_minutes - session.slot_minutes)
    row.save(update_fields=["booked_minutes"])


def person_budget(person: Person) -> int:
    """Return the person's daily budget in minutes, with the default fallback."""
    if person.daily_budget_minutes is not None:
        return person.daily_budget_minutes
    return settings.RMO_DEFAULT_DAILY_BUDGET_MINUTES


def _find_candidates(
    areas: list[CodeArea], barred: Collection[int], only_reviewer: Person | None
) -> list[_Candidate]:
    """Return owners of every area, except the barred people (INV-4), with their tier."""
    ownerships = (
        Ownership.objects.filter(area__in=areas)
        .exclude(person_id__in=barred)
        .select_related("person")
        .order_by("person_id")
    )
    roles_by_person: dict[int, list[Ownership]] = defaultdict(list)
    for ownership in ownerships:
        roles_by_person[ownership.person_id].append(ownership)
    owners = [
        rows
        for rows in roles_by_person.values()
        if len(rows) == len(areas)
        and (only_reviewer is None or rows[0].person_id == only_reviewer.pk)
    ]
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
            CapacityDay(person=person, day=day, budget_minutes=person_budget(person))
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


def _unbook(area_review: AreaReview) -> None:
    """Clear the cached booking of an area review that has no session left to book."""
    before = {
        "reviewer": area_review.reviewer_id,
        "booked_day": area_review.booked_day.isoformat(),
    }
    area_review.state = AreaReviewState.UNBOOKED
    area_review.reviewer = None
    area_review.booked_day = None
    area_review.slot_minutes = 0
    area_review.reason = ""
    area_review.unbooked_since = timezone.now()
    area_review.save(
        update_fields=[
            "state",
            "reviewer",
            "booked_day",
            "slot_minutes",
            "reason",
            "unbooked_since",
        ]
    )
    record(
        "orchestrator",
        "area_review.unbooked",
        area_review,
        before,
        None,
    )


def _book(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    area_review: AreaReview,
    candidate: _Candidate,
    row: CapacityDay,
    free: int,
    continuation: bool,
    urgent: bool,
) -> None:
    merge_request = area_review.merge_request
    reviewer_changed = candidate.person.pk != area_review.reviewer_id
    before = {
        "state": area_review.state,
        "reviewer": area_review.reviewer_id,
        "booked_day": (
            area_review.booked_day.isoformat() if area_review.booked_day else None
        ),
    }
    row.booked_minutes += settings.RMO_SLOT_MINUTES
    overage = max(
        0,
        min(
            settings.RMO_SLOT_MINUTES,
            row.booked_minutes - person_budget(candidate.person),
        ),
    )
    row.budget_minutes = person_budget(candidate.person)
    row.save(update_fields=["booked_minutes", "budget_minutes"])
    area_review.state = AreaReviewState.BOOKED
    area_review.reviewer = candidate.person
    area_review.booked_day = row.day
    area_review.slot_minutes = settings.RMO_SLOT_MINUTES
    if urgent and overage:
        area_review.reason = (
            f"{candidate.tier} owner; {row.day.isoformat()}; "
            f"urgent, {overage} minutes over budget"
        )
    else:
        area_review.reason = (
            f"{candidate.tier} owner; {row.day.isoformat()}; "
            f"{free} of {row.budget_minutes} minutes free"
        )
    area_review.unbooked_since = None
    area_review.save(
        update_fields=[
            "state",
            "reviewer",
            "booked_day",
            "slot_minutes",
            "reason",
            "unbooked_since",
        ]
    )
    ReviewSession.objects.create(
        area_review=area_review,
        reviewer=candidate.person,
        day=row.day,
        slot_minutes=area_review.slot_minutes,
        reason=area_review.reason,
        is_continuation=continuation,
        outcome=ReviewSessionOutcome.PENDING,
        is_urgent=urgent,
        overage_minutes=overage if urgent else 0,
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
    if not reviewer_changed:
        return
    outbox.enqueue(
        "set_reviewer",
        {
            "project_id": merge_request.project_id,
            "iid": merge_request.iid,
            "reviewer_gitlab_user_id": candidate.person.gitlab_user_id,
        },
    )
    continuation_suffix = " (continuation)" if continuation else ""
    queue_notification(
        [candidate.person.gitlab_user_id],
        f"You are booked to review MR {merge_request.project_id}:{merge_request.iid} "
        f"on {row.day.isoformat()}{continuation_suffix}.",
    )
