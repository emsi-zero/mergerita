from django.test import TestCase

from audit.models import AuditEvent, AuditImmutableError
from audit.services import record
from integrations.models import InboxEvent


class AuditEventTest(TestCase):
    def test_update_and_delete_raise_and_leave_row_unchanged(self) -> None:
        """RMO-18: Given an audit event, when code updates or deletes it,
        then the model raises and the row stays unchanged."""
        # given
        subject = InboxEvent.objects.create(
            source="gitlab", event_uuid="u", event_kind="k", payload={}
        )
        event = record("system", "created", subject, None, {"a": 1})
        event.action = "changed"

        # when / then
        with self.assertRaises(AuditImmutableError):
            event.save()
        with self.assertRaises(AuditImmutableError):
            event.delete()
        with self.assertRaises(AuditImmutableError):
            AuditEvent.objects.filter(pk=event.pk).update(action="changed")
        with self.assertRaises(AuditImmutableError):
            AuditEvent.objects.filter(pk=event.pk).delete()
        stored = AuditEvent.objects.get(pk=event.pk)
        self.assertEqual(stored.action, "created")
