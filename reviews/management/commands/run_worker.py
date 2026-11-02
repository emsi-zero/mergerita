import time

from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.outbox import dispatch_due
from reviews.reconciler import reconcile_all
from reviews.worker import process_next_event

IDLE_SLEEP_SECONDS = 1


class Command(BaseCommand):
    help = "Process pending inbox events and dispatch due outbox calls."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Drain pending work and exit."
        )

    def handle(self, *args: object, **options: object) -> None:
        next_reconcile = time.monotonic()
        while True:
            if time.monotonic() >= next_reconcile:
                reconcile_all()
                next_reconcile = (
                    time.monotonic() + settings.RMO_RECONCILE_INTERVAL_SECONDS
                )
            handled = process_next_event()
            dispatched = dispatch_due()
            if handled or dispatched:
                continue
            if options["once"]:
                return
            time.sleep(IDLE_SLEEP_SECONDS)
