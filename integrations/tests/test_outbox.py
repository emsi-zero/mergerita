# pylint: disable=protected-access
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from integrations import outbox
from integrations.models import OutboxCall, OutboxStatus


def failing_handler(_payload: dict) -> None:
    raise RuntimeError("chat is down")


def succeeding_handler(_payload: dict) -> None:
    return None


@override_settings(RMO_OUTBOX_MAX_ATTEMPTS=2, RMO_OUTBOX_BACKOFF_SECONDS=30)
class OutboxTest(TestCase):
    def test_failing_call_is_retried_then_failed(self) -> None:
        """RMO-16: Given a call whose handler raises, when it is dispatched, then
        it stays pending with one more attempt and a later next-attempt time;
        after the maximum attempts it is failed. A reclaim after an expired lease
        counts an attempt, and a stale worker's result is dropped."""
        started = timezone.now()
        with self.subTest(name="retry_then_failed"):
            # given
            call = outbox.enqueue("boom", {})

            with mock.patch.dict(outbox._handlers, {"boom": failing_handler}):
                # when
                outbox.dispatch_due()

                # then
                call.refresh_from_db()
                self.assertEqual(call.status, OutboxStatus.PENDING)
                self.assertEqual(call.attempts, 1)
                self.assertGreater(
                    call.next_attempt_at, started + timedelta(seconds=30)
                )
                self.assertIn("chat is down", call.last_error)

                # when the retry is due and fails again
                OutboxCall.objects.filter(pk=call.pk).update(next_attempt_at=started)
                outbox.dispatch_due()

                # then
                call.refresh_from_db()
                self.assertEqual(call.status, OutboxStatus.FAILED)
                self.assertEqual(call.attempts, 2)

        with self.subTest(name="expired_lease_counts_an_attempt"):
            # given
            call = outbox.enqueue("ok", {})
            OutboxCall.objects.filter(pk=call.pk).update(
                status=OutboxStatus.IN_FLIGHT, attempts=1, lease_until=started
            )

            # when
            with mock.patch.dict(outbox._handlers, {"ok": succeeding_handler}):
                outbox.dispatch_due()

            # then
            call.refresh_from_db()
            self.assertEqual(call.status, OutboxStatus.DONE)
            self.assertEqual(call.attempts, 2)

        with self.subTest(name="expired_lease_at_max_attempts_fails"):
            # given
            call = outbox.enqueue("ok", {})
            OutboxCall.objects.filter(pk=call.pk).update(
                status=OutboxStatus.IN_FLIGHT, attempts=2, lease_until=started
            )
            handler = mock.Mock()

            # when
            with mock.patch.dict(outbox._handlers, {"ok": handler}):
                outbox.dispatch_due()

            # then
            call.refresh_from_db()
            self.assertEqual(call.status, OutboxStatus.FAILED)
            handler.assert_not_called()

        with self.subTest(name="stale_result_is_dropped"):
            # given
            call = outbox.enqueue("slow", {})
            other_lease = started + timedelta(days=1)

            def handler_that_loses_its_lease(_payload: dict) -> None:
                OutboxCall.objects.filter(pk=call.pk).update(lease_until=other_lease)

            # when
            with mock.patch.dict(
                outbox._handlers, {"slow": handler_that_loses_its_lease}
            ):
                outbox.dispatch_due()

            # then
            call.refresh_from_db()
            self.assertEqual(call.status, OutboxStatus.IN_FLIGHT)
            self.assertEqual(call.lease_until, other_lease)
