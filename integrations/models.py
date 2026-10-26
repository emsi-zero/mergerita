from django.db import models
from django.db.models import Q


class InboxEvent(models.Model):
    """One delivered webhook, stored before processing. Unique per source and UUID (INV-1)."""

    source = models.CharField(max_length=32)
    event_uuid = models.CharField(max_length=64)
    event_kind = models.CharField(max_length=64)
    mr_key = models.CharField(max_length=64, null=True, blank=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

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
            )
        ]
