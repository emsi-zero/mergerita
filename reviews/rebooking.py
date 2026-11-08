"""Daily rebooking of unbooked area reviews (RBW-1..11, RBW-23)."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from django.db import transaction
from django.db.models import F, QuerySet
from django.db.models.expressions import OrderBy
from django.utils import timezone

from audit.services import record
from integrations.models import OutboxCall, OutboxStatus
from reviews import area_resolver, booking
from reviews.alerts import queue_admin_alert
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeRequest,
    MergeRequestState,
    RebookingSettings,
    ReviewSessionOutcome,
)

logger = logging.getLogger(__name__)

DAILY = "daily"
ADMIN = "admin"

OPEN_STATES = (MergeRequestState.AWAITING_BOOKING, MergeRequestState.IN_REVIEW)
_ACTIVE = (OutboxStatus.PENDING, OutboxStatus.IN_FLIGHT)


@dataclass
class RebookingResult:
    """What one rebooking run did."""

    booked: int = 0
    skipped: int = 0
    unbooked: list[AreaReview] = field(default_factory=list)


def run_daily_rebooking(now: datetime | None = None) -> RebookingResult | None:
    """Run the daily rebooking once per local day, at or after the run time.

    The worker calls this on every pass. The day is claimed on the settings
    row under `select_for_update` before any area review is booked, so two
    workers or two passes never both run it (INV-1, RBW-3). Return None when
    nothing ran.
    """
    local = timezone.localtime(now)
    settings_row = RebookingSettings.load()
    if not _is_due(settings_row, local):
        return None
    with transaction.atomic():
        settings_row = RebookingSettings.objects.select_for_update().get(
            pk=settings_row.pk
        )
        if not _is_due(settings_row, local):
            return None
        settings_row.last_rebooking_on = local.date()
        settings_row.save(update_fields=["last_rebooking_on"])
    return retry_unbooked(DAILY)


def retry_unbooked(
    trigger: str, merge_request_ids: list[int] | None = None
) -> RebookingResult:
    """Book each unbooked area review of an open MR, oldest wait first.

    `merge_request_ids` limits the run to those MRs (the admin action); a
    selected MR with nothing to book counts as skipped. Each area review gets
    its own transaction (INV-2). No per-review no-fit alert is sent: one admin
    summary lists what is still unbooked (RBW-10). An area review whose
    booking raises is logged and counted as still unbooked (RBW-9).
    """
    eligible = _eligible().order_by(_oldest_wait_first(), "pk")
    if merge_request_ids is not None:
        eligible = eligible.filter(merge_request_id__in=merge_request_ids)
    rows = list(eligible.values_list("pk", "merge_request_id"))
    result = RebookingResult()
    if merge_request_ids is not None:
        result.skipped = len(set(merge_request_ids) - {row[1] for row in rows})
    still_unbooked: list[int] = []
    # RBW-23 is decided only under the MR lock, in `_rebook_one`: a check here
    # would be stale by the time the lock is held.
    for area_review_id, merge_request_id in rows:
        try:
            outcome = _rebook_one(area_review_id, merge_request_id)
        except Exception:  # pylint: disable=broad-exception-caught
            # One bad row must not stop the run, nor the worker loop.
            logger.exception("rebooking of area review %s failed", area_review_id)
            outcome = None
        if outcome is True:
            result.booked += 1
        elif outcome is False:
            result.skipped += 1
        else:
            still_unbooked.append(area_review_id)
    result.unbooked = list(
        AreaReview.objects.filter(pk__in=still_unbooked)
        .select_related("merge_request")
        .prefetch_related("areas")
        .order_by(_oldest_wait_first(), "pk")
    )
    with transaction.atomic():
        record(
            "orchestrator",
            "booking.retry_run",
            RebookingSettings.load(),
            None,
            {
                "trigger": trigger,
                "booked": result.booked,
                "unbooked": len(result.unbooked),
                "skipped": result.skipped,
            },
        )
        if result.unbooked:
            queue_admin_alert(_summary(result.unbooked, timezone.localdate()))
    return result


def _rebook_one(area_review_id: int, merge_request_id: int) -> bool | None:
    """Book one area review under the locks.

    Return True when booked, False when skipped, None when still unbooked.
    Lock order (INV-2): MR row, `lock_booking()`, area review, then the capacity
    rows inside `book_area_review`. The MR state is read again in a new
    statement after the MR lock, so a change that committed during the wait is
    seen (RBW-8).
    """
    with transaction.atomic():
        merge_request = (
            MergeRequest.objects.filter(pk=merge_request_id)
            .select_for_update(of=("self",))
            .first()
        )
        # A new statement after the lock: it sees what committed during the wait.
        state = (
            MergeRequest.objects.filter(pk=merge_request_id)
            .values_list("state", flat=True)
            .first()
        )
        if (
            merge_request is None
            or state not in OPEN_STATES
            or (
                state == MergeRequestState.AWAITING_BOOKING
                and OutboxCall.objects.filter(
                    kind="resolve_areas",
                    status__in=_ACTIVE,
                    payload__merge_request_id=merge_request_id,
                ).exists()
            )
        ):
            return False
        booking.lock_booking()
        area_review = (
            AreaReview.objects.select_for_update(of=("self",))
            .filter(pk=area_review_id, state=AreaReviewState.UNBOOKED)
            .first()
        )
        if area_review is None:
            return False
        area_review.merge_request = merge_request
        if not _book(area_review):
            return None
        if (
            state == MergeRequestState.AWAITING_BOOKING
            and not merge_request.area_reviews.filter(
                state=AreaReviewState.UNBOOKED
            ).exists()
        ):
            area_resolver.mark_in_review(merge_request)
        return True


def _book(area_review: AreaReview) -> bool:
    """Try the previous reviewer first, then any eligible owner (RBW-4, RBW-5)."""
    sessions = list(area_review.sessions.order_by("created_at", "pk"))
    excluded = {
        session.reviewer_id
        for session in sessions
        if session.outcome == ReviewSessionOutcome.HANDED_BACK
    }
    continuation = bool(sessions)
    if (
        sessions
        and sessions[-1].reviewer_id not in excluded
        and booking.book_area_review(
            area_review,
            only_reviewer=sessions[-1].reviewer,
            exclude=excluded,
            continuation=continuation,
            alert_on_no_fit=False,
        )
    ):
        return True
    return booking.book_area_review(
        area_review,
        exclude=excluded,
        continuation=continuation,
        alert_on_no_fit=False,
    )


def _oldest_wait_first() -> OrderBy:
    """Order by `unbooked_since`; a NULL from an old worker during a deploy sorts first."""
    return F("unbooked_since").asc(nulls_first=True)


def _eligible() -> QuerySet[AreaReview]:
    return AreaReview.objects.filter(
        state=AreaReviewState.UNBOOKED, merge_request__state__in=OPEN_STATES
    )


def _is_due(settings_row: RebookingSettings, local: datetime) -> bool:
    return (
        local.time() >= settings_row.daily_run_time
        and settings_row.last_rebooking_on != local.date()
    )


def _summary(unbooked: list[AreaReview], today: date) -> str:
    """Build the RBW-10 admin alert: one line per area review, with its whole days waited.

    A NULL `unbooked_since` falls back to the MR's `opened_at`.
    """
    lines = [f"{len(unbooked)} area review(s) still have no reviewer:"]
    for area_review in unbooked:
        merge_request = area_review.merge_request
        since = area_review.unbooked_since or merge_request.opened_at
        days = (today - timezone.localdate(since)).days
        areas = ", ".join(area.name for area in area_review.areas.all())
        lines.append(
            f"{merge_request.project_id}:{merge_request.iid} "
            f"{merge_request.title} — {areas} — waiting {days} day(s)"
        )
    return "\n".join(lines)
