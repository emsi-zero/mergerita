from django.db import models
from django.db.models import Q
from django.utils import timezone


class InboxEvent(models.Model):
    """One delivered webhook, stored before processing. Unique per source and UUID (INV-1)."""

    source = models.CharField(max_length=32)
    event_uuid = models.CharField(max_length=64)
    event_kind = models.CharField(max_length=64)
    mr_key = models.CharField(max_length=64, null=True, blank=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "event_uuid"], name="inbox_unique_source_uuid"
            )
        ]
        indexes = [
            models.Index(
                fields=["mr_key", "id"],
                condition=Q(processed_at__isnull=True),
                name="inbox_pending_mr_idx",
            ),
            models.Index(
                fields=["id"],
                condition=Q(processed_at__isnull=True),
                name="inbox_pending_id_idx",
            ),
        ]


class OutboxStatus(models.TextChoices):  # pylint: disable=too-many-ancestors
    """Life cycle of an outbox call."""

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DONE = "done"
    FAILED = "failed"


class OutboxCall(models.Model):
    """One external call to make later, written in the same transaction as its cause (INV-5)."""

    kind = models.CharField(max_length=64)
    payload = models.JSONField()
    status = models.CharField(
        max_length=16, choices=OutboxStatus.choices, default=OutboxStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="outbox_due_idx")
        ]
