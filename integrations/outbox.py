import logging
from collections.abc import Callable
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from integrations.models import OutboxCall, OutboxStatus

logger = logging.getLogger(__name__)

Handler = Callable[[dict], None]

_handlers: dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    """Register the handler that runs calls of this kind."""
    _handlers[kind] = handler


def enqueue(kind: str, payload: dict) -> OutboxCall:
    """Queue a call. The caller owns the transaction (INV-5)."""
    return OutboxCall.objects.create(kind=kind, payload=payload)


def dispatch_due() -> int:
    """Run due calls, one at a time. Return how many were tried.

    Claim each call with a lease in a short transaction, run its handler with
    no transaction open, then record the result only if this worker still holds
    the lease.
    """
    tried = 0
    _fail_exhausted()
    while (call := _claim_one()) is not None:
        tried += 1
        try:
            _handlers[call.kind](call.payload)
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.exception("outbox call %s kind=%s failed", call.pk, call.kind)
            _record_failure(call, error)
        else:
            _record(call, status=OutboxStatus.DONE, lease_until=None)
        _fail_exhausted()
    return tried


def _fail_exhausted() -> None:
    """Fail calls whose lease expired after the last allowed attempt."""
    OutboxCall.objects.filter(
        status=OutboxStatus.IN_FLIGHT,
        lease_until__lte=timezone.now(),
        attempts__gte=settings.RMO_OUTBOX_MAX_ATTEMPTS,
    ).update(
        status=OutboxStatus.FAILED,
        lease_until=None,
        last_error="lease expired after the last attempt",
    )


def _claim_one() -> OutboxCall | None:
    """Lease the oldest due call and count the attempt now."""
    now = timezone.now()
    due = Q(status=OutboxStatus.PENDING, next_attempt_at__lte=now) | Q(
        status=OutboxStatus.IN_FLIGHT, lease_until__lte=now
    )
    with transaction.atomic():
        call = (
            OutboxCall.objects.filter(due)
            .order_by("id")
            .select_for_update(skip_locked=True)
            .first()
        )
        if call is None:
            return None
        call.status = OutboxStatus.IN_FLIGHT
        call.attempts += 1
        call.lease_until = now + timedelta(seconds=settings.RMO_OUTBOX_LEASE_SECONDS)
        call.save(update_fields=["status", "attempts", "lease_until"])
    return call


def _record(call: OutboxCall, **fields: object) -> None:
    """Write a result only while this worker holds the lease. Drop it otherwise."""
    written = OutboxCall.objects.filter(
        pk=call.pk, status=OutboxStatus.IN_FLIGHT, lease_until=call.lease_until
    ).update(**fields)
    if written == 0:
        logger.warning("outbox call %s lost its lease, result dropped", call.pk)


def _record_failure(call: OutboxCall, error: Exception) -> None:
    exhausted = call.attempts >= settings.RMO_OUTBOX_MAX_ATTEMPTS
    backoff_seconds = settings.RMO_OUTBOX_BACKOFF_SECONDS * 2**call.attempts
    _record(
        call,
        status=OutboxStatus.FAILED if exhausted else OutboxStatus.PENDING,
        next_attempt_at=timezone.now() + timedelta(seconds=backoff_seconds),
        lease_until=None,
        last_error=f"{type(error).__name__}: {error}",
    )
