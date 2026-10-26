import threading
from unittest import mock

from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings

from audit.models import AuditEvent
from integrations.inbox import store_event
from integrations.models import InboxEvent, OutboxCall
from reviews.lifecycle import handle_event as real_handle
from reviews.models import MergeRequest, MergeRequestState, Person
from reviews.worker import process_next_event


def store_open_event(uuid: str, project_id: int, iid: int, author_id: int) -> None:
    payload = {
        "project": {"id": project_id},
        "object_attributes": {
            "iid": iid,
            "action": "open",
            "author_id": author_id,
            "title": f"MR {iid}",
        },
    }
    store_event("gitlab", uuid, "Merge Request Hook", f"{project_id}:{iid}", payload)


@override_settings(RMO_INBOX_CLAIM_GRACE_SECONDS=0)
class WorkerTest(TransactionTestCase):
    def setUp(self) -> None:
        Person.objects.create(gitlab_user_id=11, name="Author")

    def test_open_event_creates_merge_request(self) -> None:
        """RMO-4: Given a pending open event for an unknown MR, when the worker
        runs, then the MR exists in awaiting_ai_review and the event is processed."""
        # given
        store_open_event("u-4", 7, 3, 11)

        # when
        handled = process_next_event()

        # then
        self.assertTrue(handled)
        merge_request = MergeRequest.objects.get(project_id=7, iid=3)
        self.assertEqual(merge_request.state, MergeRequestState.AWAITING_AI_REVIEW)
        self.assertEqual(merge_request.author.gitlab_user_id, 11)
        self.assertIsNotNone(InboxEvent.objects.get().processed_at)
        self.assertEqual(AuditEvent.objects.filter(action="mr.opened").count(), 1)

    def test_two_workers_process_each_event_once(self) -> None:
        """RMO-5: Given pending events and two concurrent workers, when both
        drain the inbox, then each event is handled exactly once."""
        # given
        for iid in range(1, 7):
            store_open_event(f"u-5-{iid}", 7, iid, 11)
        handled_ids: list[int] = []

        def slow_handle(event: InboxEvent) -> None:
            handled_ids.append(event.pk)
            threading.Event().wait(0.05)
            real_handle(event)

        def drain() -> None:
            try:
                while process_next_event():
                    pass
            finally:
                connection.close()

        # when
        with mock.patch("reviews.worker.handle_event", slow_handle):
            threads = [threading.Thread(target=drain) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        # then
        self.assertEqual(len(handled_ids), 6)
        self.assertEqual(len(set(handled_ids)), 6)
        self.assertEqual(MergeRequest.objects.count(), 6)
        self.assertEqual(
            InboxEvent.objects.filter(processed_at__isnull=True).count(), 0
        )

    def test_later_event_waits_for_earlier_event_of_same_mr(self) -> None:
        """RMO-6: Given event 1 of an MR is in flight in another worker, when a
        worker looks for work, then it does not take event 2 of the same MR.
        A delivery younger than the claim grace is not taken either."""
        # given
        first = InboxEvent.objects.create(
            source="gitlab",
            event_uuid="u-6-1",
            event_kind="Push Hook",
            mr_key="7:3",
            payload={},
        )
        second = InboxEvent.objects.create(
            source="gitlab",
            event_uuid="u-6-2",
            event_kind="Push Hook",
            mr_key="7:3",
            payload={},
        )
        locked = threading.Event()
        release = threading.Event()

        def hold_first_event() -> None:
            try:
                with transaction.atomic():
                    InboxEvent.objects.select_for_update().get(pk=first.pk)
                    locked.set()
                    release.wait(timeout=10)
            finally:
                connection.close()

        holder = threading.Thread(target=hold_first_event)
        holder.start()
        locked.wait(timeout=10)

        # when
        handled_while_locked = process_next_event()
        release.set()
        holder.join()

        # then
        self.assertFalse(handled_while_locked)
        second.refresh_from_db()
        self.assertIsNone(second.processed_at)
        self.assertTrue(process_next_event())
        first.refresh_from_db()
        self.assertIsNotNone(first.processed_at)
        with self.settings(RMO_INBOX_CLAIM_GRACE_SECONDS=60):
            InboxEvent.objects.create(
                source="gitlab",
                event_uuid="u-6-3",
                event_kind="Push Hook",
                mr_key="8:1",
                payload={},
            )
            self.assertFalse(process_next_event())

    def test_failing_event_is_quarantined(self) -> None:
        """RMO-21: Given an event that raises (unknown author) with a later event
        of the same MR and an event of another MR, when the worker runs, then the
        failed event is rolled back, marked failed, an admin alert is queued, the
        other MR is processed, and the later event stays pending."""
        # given
        Person.objects.create(gitlab_user_id=1, name="Admin", is_admin=True)
        store_open_event("u-21-1", 7, 1, 999)
        store_event("gitlab", "u-21-2", "Push Hook", "7:1", {})
        store_open_event("u-21-3", 7, 2, 11)

        def write_then_handle(event: InboxEvent) -> None:
            OutboxCall.objects.create(kind="marker", payload={"event": event.pk})
            real_handle(event)

        # when
        with mock.patch("reviews.worker.handle_event", write_then_handle):
            results = [process_next_event() for _ in range(3)]

        # then
        self.assertEqual(results, [True, True, False])
        failed = InboxEvent.objects.get(event_uuid="u-21-1")
        self.assertIsNotNone(failed.failed_at)
        self.assertIsNone(failed.processed_at)
        self.assertIn("DoesNotExist", failed.last_error)
        self.assertFalse(MergeRequest.objects.filter(iid=1).exists())
        self.assertTrue(MergeRequest.objects.filter(iid=2).exists())
        self.assertIsNone(InboxEvent.objects.get(event_uuid="u-21-2").processed_at)
        handled_event = InboxEvent.objects.get(event_uuid="u-21-3")
        self.assertEqual(
            list(
                OutboxCall.objects.filter(kind="marker").values_list(
                    "payload__event", flat=True
                )
            ),
            [handled_event.pk],
        )
        alert = OutboxCall.objects.get(kind="notify")
        self.assertEqual(alert.payload["gitlab_user_ids"], [1])
        self.assertEqual(OutboxCall.objects.count(), 2)
