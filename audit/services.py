from django.db.models import Model

from audit.models import AuditEvent


def record(
    actor: str, action: str, subject: Model, before: dict | None, after: dict | None
) -> AuditEvent:
    """Write one audit event. The caller owns the transaction."""
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        subject_type=subject._meta.label,  # pylint: disable=protected-access
        subject_id=str(subject.pk),
        before=before,
        after=after,
    )
