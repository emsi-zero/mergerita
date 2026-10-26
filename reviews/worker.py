import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from integrations.models import InboxEvent
from reviews.lifecycle import handle_event

logger = logging.getLogger(__name__)


def process_next_event() -> bool:
    """Handle the oldest claimable pending event. Return False when none is claimable.

    An event is claimable when it is the oldest pending event of its MR, so a
    second worker never takes event 2 while event 1 of the same MR is in flight.
    Events younger than the claim grace are left alone, so an earlier delivery
    whose insert has not committed yet is not overtaken by a later one.
    """
    pending = InboxEvent.objects.filter(processed_at__isnull=True)
    settled_before = timezone.now() - timedelta(
        seconds=settings.RMO_INBOX_CLAIM_GRACE_SECONDS
    )
    oldest_of_mr = pending.filter(mr_key=OuterRef("mr_key")).order_by("id")
    with transaction.atomic():
        event = (
            pending.filter(
                Q(received_at__lte=settled_before),
                Q(mr_key__isnull=True) | Q(id=Subquery(oldest_of_mr.values("id")[:1])),
            )
            .order_by("id")
            .select_for_update(skip_locked=True)
            .first()
        )
        if event is None:
            return False
        handle_event(event)
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
    logger.info(
        "processed event %s kind=%s mr_key=%s", event.pk, event.event_kind, event.mr_key
    )
    return True
