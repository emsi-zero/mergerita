import time

from django.core.management.base import BaseCommand

from reviews.worker import process_next_event

IDLE_SLEEP_SECONDS = 1


class Command(BaseCommand):
    help = "Process pending inbox events."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Drain pending events and exit."
        )

    def handle(self, *args: object, **options: object) -> None:
        while True:
            if process_next_event():
                continue
            if options["once"]:
                return
            time.sleep(IDLE_SLEEP_SECONDS)
