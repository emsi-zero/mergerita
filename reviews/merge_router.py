"""Merge router: give an approved MR to a merge owner and a merge window (MRG-1..3)."""

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from audit.services import record
from reviews.alerts import queue_admin_alert, queue_notification
from reviews.models import (
    AreaReview,
    Leave,
    MergeAssignment,
    MergeRequest,
    MergeRoutingSettings,
    Person,
)


def assign_merge(
    merge_request: MergeRequest,
    *,
    alert_on_no_fit: bool = True,
    urgent: bool | None = None,
) -> MergeAssignment | None:
    """Assign the MR to a merge owner and window. Return None when nobody fits.

    Candidates are merge owners who are not the author and not a current
    reviewer. Each candidate gets the earliest window at or after now, within
    the booking horizon, on a working day without leave, where they are under
    their daily limit. The best candidate has the earliest window day, then the
    fewest assignments that day, then the earliest window, then the lowest
    person id. A reason is stored, the merge owner is told, and an audit event
    is written. When nobody fits, an admin alert is queued, unless
    `alert_on_no_fit` is false (the retry sends one summary instead, MRDR-3).

    An urgent MR (`urgent`, or `urgent_at` set when `urgent` is None) ignores
    the daily merge limit and takes the earliest window from now. Every other
    rule stays. The assignment records `over_limit` when the limit was
    reached (URG-23).

    The caller owns the transaction and already holds the MR lock and
    `booking.lock_booking()`: the count and the insert are then serial (INV-1).
    """
    barred = {
        merge_request.author_id,
        *AreaReview.objects.filter(
            merge_request=merge_request, reviewer__isnull=False
        ).values_list("reviewer_id", flat=True),
    }
    candidates = list(
        Person.objects.filter(is_merge_owner=True).exclude(pk__in=barred).order_by("pk")
    )
    if urgent is None:
        urgent = merge_request.urgent_at is not None
    fit = _best_fit(candidates, timezone.now(), urgent)
    if fit is None:
        record(
            "orchestrator",
            "merge.unassigned",
            merge_request,
            None,
            {
                "horizon_days": settings.RMO_BOOKING_HORIZON_DAYS,
                "retry": not alert_on_no_fit,
            },
        )
        if alert_on_no_fit:
            queue_admin_alert(
                f"MR {merge_request.project_id}:{merge_request.iid}: no merge owner "
                f"fits within {settings.RMO_BOOKING_HORIZON_DAYS} days."
            )
        return None
    person, window, used, limit = fit
    if person.pk in barred:
        raise ValueError("The author or a reviewer cannot be a merge owner (INV-2).")
    local_window = timezone.localtime(window)
    over_limit = used >= limit
    reason = (
        f"Earliest window {local_window:%H:%M} on {local_window.date().isoformat()}; "
        f"{used} of {limit} merges that day"
    )
    if over_limit:
        reason += " (urgent, over the daily limit)"
    assignment = MergeAssignment.objects.create(
        merge_request=merge_request,
        merge_owner=person,
        window=window,
        reason=reason,
        over_limit=over_limit,
    )
    record(
        "orchestrator",
        "merge.assigned",
        assignment,
        None,
        {
            "merge_request": merge_request.pk,
            "merge_owner": person.pk,
            "window": window.isoformat(),
            "reason": assignment.reason,
        },
    )
    queue_notification(
        [person.gitlab_user_id],
        f"Merge !{merge_request.iid} {merge_request.title} in your "
        f"{local_window:%H:%M} window on {local_window.date().isoformat()}.",
    )
    return assignment


def cancel_open_assignment(merge_request: MergeRequest, reason: str) -> None:
    """Cancel the MR's open assignment, if any, and tell its merge owner (MRG-11).

    The caller owns the transaction and holds the MR lock.
    """
    assignment = (
        MergeAssignment.objects.select_related("merge_owner")
        .filter(
            merge_request=merge_request,
            merged_at__isnull=True,
            cancelled_at__isnull=True,
        )
        .first()
    )
    if assignment is None:
        return
    assignment.cancelled_at = timezone.now()
    assignment.save(update_fields=["cancelled_at"])
    record(
        "orchestrator",
        "merge.cancelled",
        assignment,
        {"merge_owner": assignment.merge_owner_id},
        {"cancelled_at": assignment.cancelled_at.isoformat(), "reason": reason},
    )
    if assignment.merge_owner is not None:
        queue_notification(
            [assignment.merge_owner.gitlab_user_id],
            f"Do not merge !{merge_request.iid} {merge_request.title}: {reason}.",
        )


def _best_fit(
    candidates: list[Person], now: datetime, urgent: bool = False
) -> tuple[Person, datetime, int, int] | None:
    """Return the best (person, window, assignments that day, limit), or None."""
    today = timezone.localdate(now)
    days = [today + timedelta(days=n) for n in range(settings.RMO_BOOKING_HORIZON_DAYS)]
    counts = _count_assignments(candidates, days)
    default_limit = MergeRoutingSettings.load().default_daily_merge_limit
    leave_ranges = _leave_ranges(candidates, days)
    fits = []
    for person in candidates:
        limit = (
            person.daily_merge_limit
            if person.daily_merge_limit is not None
            else default_limit
        )
        if limit == 0:
            continue  # opted out of merging, even for urgent MRs (UGF-1)
        for window in _windows(person, days, leave_ranges[person.pk], now):
            day = timezone.localtime(window).date()
            used = counts[(person.pk, day)]
            if urgent or used < limit:
                key = (
                    (window, used, person.pk)
                    if urgent
                    else (day, used, window, person.pk)
                )
                fits.append((key, person, window, used, limit))
                break
    if not fits:
        return None
    _, person, window, used, limit = min(fits, key=lambda fit: fit[0])
    return person, window, used, limit


def _windows(
    person: Person,
    days: list[date],
    leaves: list[tuple[date, date]],
    now: datetime,
) -> list[datetime]:
    """Return the person's merge windows in time order, from now on, as aware datetimes."""
    windows = [
        timezone.make_aware(datetime.combine(day, time.fromisoformat(text)))
        for day in days
        if day.weekday() in person.working_days
        and not any(start <= day <= end for start, end in leaves)
        for text in person.merge_times
    ]
    return sorted(window for window in windows if window >= now)


def _leave_ranges(
    candidates: list[Person], days: list[date]
) -> dict[int, list[tuple[date, date]]]:
    leave_ranges = defaultdict(list)
    for leave in Leave.objects.filter(
        person__in=candidates, start_date__lte=days[-1], end_date__gte=days[0]
    ):
        leave_ranges[leave.person_id].append((leave.start_date, leave.end_date))
    return leave_ranges


def _count_assignments(
    candidates: list[Person], days: list[date]
) -> Counter[tuple[int, date]]:
    """Count the non-cancelled assignments per (person id, local day) in the horizon."""
    start = timezone.make_aware(datetime.combine(days[0], time.min))
    end = timezone.make_aware(datetime.combine(days[-1] + timedelta(days=1), time.min))
    windows = MergeAssignment.objects.filter(
        merge_owner__in=candidates,
        cancelled_at__isnull=True,
        window__gte=start,
        window__lt=end,
    ).values_list("merge_owner_id", "window")
    return Counter(
        (person_id, timezone.localtime(window).date()) for person_id, window in windows
    )
