"""Retry merge routing for approved MRs that have no merge owner (MRDR-1..5)."""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from audit.services import record
from reviews import booking, merge_router
from reviews.alerts import queue_admin_alert
from reviews.models import (
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    MergeRoutingSettings,
)

logger = logging.getLogger(__name__)

DAILY = "daily"
ADMIN = "admin"


@dataclass
class RetryResult:
    """What one retry run did."""

    assigned: int = 0
    skipped: int = 0
    unassigned: list[MergeRequest] = field(default_factory=list)


def run_daily_retry(now: datetime | None = None) -> RetryResult | None:
    """Run the daily retry once per local day, at or after the retry time.

    The worker calls this on every pass. The day is claimed on the settings
    row under `select_for_update` before any MR is routed, so two workers or
    two passes never both run it (INV-3, MRDR-2). Return None when nothing ran.
    """
    local = timezone.localtime(now)
    settings_row = MergeRoutingSettings.load()
    if not _is_due(settings_row, local):
        return None
    with transaction.atomic():
        settings_row = MergeRoutingSettings.objects.select_for_update().get(
            pk=settings_row.pk
        )
        if not _is_due(settings_row, local):
            return None
        settings_row.last_retry_on = local.date()
        settings_row.save(update_fields=["last_retry_on"])
    result = retry_unassigned(DAILY)
    if result.unassigned:
        with transaction.atomic():
            queue_admin_alert(_summary(result.unassigned, local))
    return result


def retry_unassigned(
    trigger: str, merge_request_ids: list[int] | None = None
) -> RetryResult:
    """Route each approved MR with no open assignment, one transaction per MR.

    `merge_request_ids` limits the run to those MRs (the admin action); the
    others in it are counted as skipped. Each MR is checked again under the MR
    lock, then routed under `booking.lock_booking()`, in the coverage lock
    order (MRDR-5, INV-1, INV-2). No per-MR no-fit alert is sent (MRDR-3). An
    MR whose routing raises is logged and counted as still unassigned.
    """
    eligible = _eligible().order_by("pk")
    if merge_request_ids is not None:
        eligible = eligible.filter(pk__in=merge_request_ids)
    eligible_ids = list(eligible.values_list("pk", flat=True))
    result = RetryResult()
    if merge_request_ids is not None:
        result.skipped = len(set(merge_request_ids)) - len(eligible_ids)
    for merge_request_id in eligible_ids:
        try:
            _route_one(merge_request_id, result)
        except Exception:  # pylint: disable=broad-exception-caught
            # One bad MR must not stop the run, nor the worker loop. It is
            # listed as still unassigned, so the summary shows it.
            logger.exception("merge retry of MR %s failed", merge_request_id)
            result.unassigned.append(MergeRequest.objects.get(pk=merge_request_id))
    with transaction.atomic():
        record(
            "orchestrator",
            "merge.retry_run",
            MergeRoutingSettings.load(),
            None,
            {
                "trigger": trigger,
                "assigned": result.assigned,
                "unassigned": len(result.unassigned),
                "skipped": result.skipped,
            },
        )
    return result


def _route_one(merge_request_id: int, result: RetryResult) -> None:
    """Lock the MR, check it again, then route it under the booking lock.

    The check is a separate statement after the lock, so its snapshot sees an
    assignment or a state change that another transaction committed while this
    one waited for the lock (MRDR-5, INV-2).
    """
    with transaction.atomic():
        merge_request = (
            MergeRequest.objects.select_for_update(of=("self",))
            .filter(pk=merge_request_id)
            .first()
        )
        if (
            merge_request is None
            or not _eligible().filter(pk=merge_request_id).exists()
        ):
            result.skipped += 1
            return
        booking.lock_booking()
        if merge_router.assign_merge(merge_request, alert_on_no_fit=False):
            result.assigned += 1
        else:
            result.unassigned.append(merge_request)


def _eligible() -> QuerySet[MergeRequest]:
    open_assignment = MergeAssignment.objects.filter(
        merge_request=OuterRef("pk"),
        merged_at__isnull=True,
        cancelled_at__isnull=True,
    )
    return MergeRequest.objects.filter(state=MergeRequestState.APPROVED).exclude(
        Exists(open_assignment)
    )


def _is_due(settings_row: MergeRoutingSettings, local: datetime) -> bool:
    return (
        local.time() >= settings_row.daily_retry_time
        and settings_row.last_retry_on != local.date()
    )


def _summary(unassigned: list[MergeRequest], local: datetime) -> str:
    """Build the MRDR-3 admin alert: one line per MR, with its whole days waited."""
    lines = [f"{len(unassigned)} approved MR(s) still have no merge owner:"]
    for merge_request in unassigned:
        if merge_request.approved_at is None:
            waited = "waiting since an unknown date"
        else:
            days = (local.date() - timezone.localdate(merge_request.approved_at)).days
            waited = f"waiting {days} day(s)"
        lines.append(
            f"{merge_request.project_id}:{merge_request.iid} "
            f"{merge_request.title} — {waited}"
        )
    return "\n".join(lines)
