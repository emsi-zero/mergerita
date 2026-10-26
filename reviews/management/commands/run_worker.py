import time

from django.core.management.base import BaseCommand

from integrations.outbox import dispatch_due
from reviews.worker import process_next_event

IDLE_SLEEP_SECONDS = 1


class Command(BaseCommand):
    help = "Process pending inbox events and dispatch due outbox calls."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Drain pending work and exit."
        )

    def handle(self, *args: object, **options: object) -> None:
        while True:
            handled = process_next_event()
            dispatched = dispatch_due()
            if handled or dispatched:
                continue
            if options["once"]:
                return
            time.sleep(IDLE_SLEEP_SECONDS)
