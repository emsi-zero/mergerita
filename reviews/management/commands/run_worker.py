import logging
import time
from collections.abc import Callable

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from integrations.outbox import dispatch_due
from reviews.alerts import queue_admin_alert
from reviews.merge_retry import run_daily_retry
from reviews.rebooking import run_daily_rebooking
from reviews.reconciler import reconcile_all
from reviews.urgency import run_urgency_check
from reviews.watchdog import run_daily_watchdog
from reviews.worker import process_next_event

IDLE_SLEEP_SECONDS = 1

logger = logging.getLogger(__name__)


def run_guarded(name: str, job: Callable[[], object], failing: set[str]) -> None:
    """Run one scheduled job so that its exception never leaves the worker loop.

    A failure alerts the admins once per failing streak: `failing` holds the
    jobs already alerted, and a job leaves it when it runs once without
    raising (RWF-4, RWF-5). The first failure of a streak logs the traceback,
    later ones a single line. A daily job's claim is not undone, so it does not
    run again that day (RWF-6).
    """
    try:
        job()
    except Exception as error:  # pylint: disable=broad-exception-caught
        _drop_dead_connection()
        if name in failing:
            logger.error("worker job %s failed again: %r", name, error)
        else:
            logger.exception("worker job %s failed", name)
            if _alert(name, error):
                failing.add(name)
        return
    failing.discard(name)


def _drop_dead_connection() -> None:
    """Close a connection the database dropped, so Django reconnects on the next query."""
    if connection.connection is not None and not connection.is_usable():
        connection.close()


def _alert(name: str, error: Exception) -> bool:
    """Queue one admin alert in its own transaction (INV-2). Return True when it committed."""
    try:
        with transaction.atomic():
            queue_admin_alert(
                f"Worker job {name} failed: {type(error).__name__}: {error}. "
                "It is skipped until its next run; see the worker log."
            )
    except Exception:  # pylint: disable=broad-exception-caught
        # The database may be the cause. The next failure tries to alert again.
        logger.exception("could not queue the alert for worker job %s", name)
        _drop_dead_connection()
        return False
    return True


class Command(BaseCommand):
    help = "Process pending inbox events and dispatch due outbox calls."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Drain pending work and exit."
        )

    def handle(self, *args: object, **options: object) -> None:
        failing: set[str] = set()
        next_reconcile = time.monotonic()
        next_urgency_check = time.monotonic()
        while True:
            if time.monotonic() >= next_reconcile:
                run_guarded("reconcile_all", reconcile_all, failing)
                next_reconcile = (
                    time.monotonic() + settings.RMO_RECONCILE_INTERVAL_SECONDS
                )
            if time.monotonic() >= next_urgency_check:
                run_guarded("run_urgency_check", run_urgency_check, failing)
                next_urgency_check = (
                    time.monotonic() + settings.RMO_URGENCY_CHECK_INTERVAL_SECONDS
                )
            run_guarded("run_daily_retry", run_daily_retry, failing)
            run_guarded("run_daily_rebooking", run_daily_rebooking, failing)
            run_guarded("run_daily_watchdog", run_daily_watchdog, failing)
            handled = process_next_event()
            dispatched = dispatch_due()
            if handled or dispatched:
                continue
            if options["once"]:
                return
            time.sleep(IDLE_SLEEP_SECONDS)
