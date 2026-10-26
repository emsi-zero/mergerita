from typing import NoReturn

from django.db import models


class AuditImmutableError(Exception):
    """Raised when code tries to change or delete an audit event."""


class AuditQuerySet(models.QuerySet):
    """Queryset that refuses every write except a plain insert."""

    def update(self, **kwargs: object) -> NoReturn:
        raise AuditImmutableError("Audit events cannot be updated.")

    def delete(self) -> NoReturn:
        raise AuditImmutableError("Audit events cannot be deleted.")

    def bulk_create(self, objs, *args: object, **kwargs: object) -> list:
        if kwargs.get("update_conflicts"):
            raise AuditImmutableError("Audit events cannot be updated.")
        return super().bulk_create(objs, *args, **kwargs)


class AuditEvent(models.Model):
    """One state change. Append-only: rows are never updated or deleted (INV-6)."""

    actor = models.CharField(max_length=128)
    action = models.CharField(max_length=64)
    subject_type = models.CharField(max_length=64)
    subject_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        base_manager_name = "objects"

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk is not None:
            raise AuditImmutableError("Audit events cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args: object, **kwargs: object) -> NoReturn:
        raise AuditImmutableError("Audit events cannot be deleted.")
