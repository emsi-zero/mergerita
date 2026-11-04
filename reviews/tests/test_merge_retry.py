import threading
import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from audit.models import AuditEvent
from integrations.models import OutboxCall
from reviews import merge_retry
from reviews.models import (
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    MergeRoutingSettings,
    Person,
)
from reviews.tests.test_merge_router import merge_owner

TEHRAN = ZoneInfo("Asia/Tehran")
RETRY_TIME = datetime(2026, 11, 4, 8, 0, tzinfo=TEHRAN)  # a Wednesday


def create_merge_request(
    author: Person, iid: int, state: str = MergeRequestState.APPROVED, **fields: object
) -> MergeRequest:
    return MergeRequest.objects.create(
        project_id=7, iid=iid, author=author, title=f"MR {iid}", state=state, **fields
    )


def notify_texts() -> list[str]:
    return [
        call.payload["text"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def at(when: datetime):
    return mock.patch("django.utils.timezone.now", return_value=when)


class DailyRetryTest(TestCase):
    """The daily retry of unassigned approved MRs."""

    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")
        self.admin = Person.objects.create(
            gitlab_user_id=99, name="Admin", is_admin=True
        )

    def test_mrdr_1_daily_retry_assigns_approved_mrs_without_a_merge_owner(
        self,
    ) -> None:
        """MRDR-1: Given an approved MR with no open assignment and a merge owner
        who now fits, when the local time reaches the daily retry time, then the
        MR is assigned with a window, a reason, a chat message and an audit
        event; before that time nothing runs."""
        # given
        owner = merge_owner(21)
        merge_request = create_merge_request(self.author, 1)
        assigned_elsewhere = create_merge_request(self.author, 2)
        other_owner = merge_owner(22)
        MergeAssignment.objects.create(
            merge_request=assigned_elsewhere,
            merge_owner=other_owner,
            window=RETRY_TIME + timedelta(hours=2),
            reason="earlier",
        )

        # when / then
        with self.subTest("before the retry time"):
            with at(RETRY_TIME - timedelta(minutes=1)):
                self.assertIsNone(merge_retry.run_daily_retry())

            self.assertFalse(
                MergeAssignment.objects.filter(merge_request=merge_request)
            )
        with self.subTest("at the retry time"):
            with at(RETRY_TIME):
                result = merge_retry.run_daily_retry()

            assignment = MergeAssignment.objects.get(merge_request=merge_request)
            self.assertEqual(result.assigned, 1)
            self.assertEqual(assignment.merge_owner, owner)
            self.assertEqual(
                assignment.window, datetime(2026, 11, 4, 10, 0, tzinfo=TEHRAN)
            )
            self.assertTrue(assignment.reason)
            self.assertEqual(
                MergeAssignment.objects.filter(
                    merge_request=assigned_elsewhere
                ).count(),
                1,
            )
            self.assertIn(
                "Merge !1 MR 1 in your 10:00 window on 2026-11-04.", notify_texts()
            )
            self.assertTrue(
                AuditEvent.objects.filter(
                    action="merge.assigned", subject_id=str(assignment.pk)
                ).exists()
            )
            run = AuditEvent.objects.get(action="merge.retry_run")
            self.assertEqual(run.after["trigger"], "daily")
            self.assertEqual(run.after["assigned"], 1)

    def test_mrdr_3_daily_retry_sends_one_summary_of_still_unassigned_mrs(
        self,
    ) -> None:
        """MRDR-3: Given two approved MRs that no merge owner fits, one approved
        three days ago and one with no approval time, when the daily retry runs,
        then one admin alert lists both with their wait and no per-MR no-fit
        alert is sent; when every MR fits, no summary is sent."""
        # given
        create_merge_request(self.author, 1, approved_at=RETRY_TIME - timedelta(days=3))
        create_merge_request(self.author, 2)

        # when
        with at(RETRY_TIME):
            result = merge_retry.run_daily_retry()

        # then
        self.assertEqual(len(result.unassigned), 2)
        self.assertEqual(
            notify_texts(),
            [
                "2 approved MR(s) still have no merge owner:\n"
                "7:1 MR 1 — waiting 3 day(s)\n"
                "7:2 MR 2 — waiting since an unknown date"
            ],
        )
        self.assertEqual(
            OutboxCall.objects.get(kind="notify").payload["gitlab_user_ids"], [99]
        )
        unassigned = AuditEvent.objects.filter(action="merge.unassigned")
        self.assertEqual(unassigned.count(), 2)
        self.assertTrue(all(event.after["retry"] for event in unassigned))
        with self.subTest("no summary when every MR fits"):
            OutboxCall.objects.all().delete()
            merge_owner(21, merge_times=["10:00"], daily_merge_limit=2)

            with at(RETRY_TIME + timedelta(days=1)):
                result = merge_retry.run_daily_retry()

            self.assertEqual(result.assigned, 2)
            self.assertFalse(
                [text for text in notify_texts() if "still have no merge owner" in text]
            )
        with self.subTest("a routing error is listed and the run goes on"):
            OutboxCall.objects.all().delete()
            create_merge_request(self.author, 3)
            create_merge_request(self.author, 4)

            with (
                at(RETRY_TIME + timedelta(days=2)),
                self.assertLogs("reviews.merge_retry", "ERROR"),
                mock.patch.object(
                    merge_retry.merge_router,
                    "assign_merge",
                    side_effect=[RuntimeError("boom"), None],
                ),
            ):
                result = merge_retry.run_daily_retry()

            self.assertEqual(
                [merge_request.iid for merge_request in result.unassigned], [3, 4]
            )
            self.assertIn("7:3 MR 3", notify_texts()[0])
            self.assertEqual(
                AuditEvent.objects.filter(action="merge.retry_run").count(), 3
            )


def wait_until_blocked_on_a_lock() -> None:
    """Return once another connection waits on a row lock (or fail after 5 s)."""
    deadline = time_module.monotonic() + 5
    while time_module.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND datname = current_database()"
            )
            if cursor.fetchone()[0]:
                return
        time_module.sleep(0.02)
    raise AssertionError("no connection waited on the lock")


class DailyRetryConcurrencyTest(TransactionTestCase):
    """The retry next to other workers and other paths."""

    def test_mrdr_2_daily_retry_runs_once_per_day_and_catches_up_after_downtime(
        self,
    ) -> None:
        """MRDR-2: Given two workers, when both pass the retry time at the same
        moment, then exactly one runs the retry; a later pass the same day does
        nothing; after a day the worker was down at the retry time, its first
        pass later that day runs the retry."""
        # given
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        merge_owner(21)
        create_merge_request(author, 1)
        MergeRoutingSettings.load()
        barrier = threading.Barrier(2)

        def run(_: int) -> merge_retry.RetryResult | None:
            try:
                barrier.wait()
                return merge_retry.run_daily_retry()
            finally:
                connection.close()

        # when
        with at(RETRY_TIME), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))

        # then
        self.assertEqual([result is None for result in results].count(True), 1)
        self.assertEqual(AuditEvent.objects.filter(action="merge.retry_run").count(), 1)
        self.assertEqual(MergeAssignment.objects.count(), 1)
        with self.subTest("a second pass the same day"):
            with at(RETRY_TIME + timedelta(hours=8)):
                self.assertIsNone(merge_retry.run_daily_retry())

            self.assertEqual(
                AuditEvent.objects.filter(action="merge.retry_run").count(), 1
            )
        with self.subTest("late on a day after downtime at the retry time"):
            with at(RETRY_TIME + timedelta(days=1, hours=7)):
                self.assertIsNotNone(merge_retry.run_daily_retry())

            self.assertEqual(
                MergeRoutingSettings.load().last_retry_on,
                RETRY_TIME.date() + timedelta(days=1),
            )

    def test_mrdr_5_retry_skips_mrs_changed_while_it_waits_for_the_lock(
        self,
    ) -> None:
        """MRDR-5: Given an approved MR selected for a retry, when another path
        holds its lock and merges it, revokes it or assigns it, and commits while
        the retry waits for the lock, then the retry skips it and adds no
        assignment."""
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        owner = merge_owner(21)
        for iid, change in (
            (1, {"state": MergeRequestState.MERGED}),
            (2, {"state": MergeRequestState.IN_REVIEW}),
            (3, "assigned"),
        ):
            with self.subTest(change=change):
                # given
                merge_request = create_merge_request(author, iid)
                locked = threading.Event()
                release = threading.Event()

                def other_path(
                    merge_request=merge_request,
                    change=change,
                    locked=locked,
                    release=release,
                ) -> None:
                    try:
                        with transaction.atomic():
                            MergeRequest.objects.select_for_update().get(
                                pk=merge_request.pk
                            )
                            if change == "assigned":
                                MergeAssignment.objects.create(
                                    merge_request=merge_request,
                                    merge_owner=owner,
                                    window=RETRY_TIME + timedelta(hours=2),
                                    reason="another path",
                                )
                            else:
                                MergeRequest.objects.filter(pk=merge_request.pk).update(
                                    **change
                                )
                            locked.set()
                            release.wait(5)
                    finally:
                        connection.close()

                def retry(merge_request=merge_request) -> merge_retry.RetryResult:
                    try:
                        return merge_retry.retry_unassigned(
                            merge_retry.ADMIN, [merge_request.pk]
                        )
                    finally:
                        connection.close()

                # when
                with at(RETRY_TIME), ThreadPoolExecutor(max_workers=2) as pool:
                    holder = pool.submit(other_path)
                    locked.wait(5)
                    retrying = pool.submit(retry)
                    wait_until_blocked_on_a_lock()
                    release.set()
                    holder.result(timeout=10)
                    result = retrying.result(timeout=10)

                # then
                self.assertEqual(
                    (result.assigned, len(result.unassigned), result.skipped),
                    (0, 0, 1),
                )
                self.assertEqual(
                    MergeAssignment.objects.filter(merge_request=merge_request).count(),
                    1 if change == "assigned" else 0,
                )


class RetryAdminTest(TestCase):
    """The admin side of the retry."""

    def setUp(self) -> None:
        admin = get_user_model().objects.create_superuser("root", password="pw")
        self.client.force_login(admin)
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")

    def test_mrdr_4_admin_action_retries_selected_mrs_and_reports_counts(
        self,
    ) -> None:
        """MRDR-4: Given selected MRs that are approved and unassigned, already
        assigned, or not approved, when the admin runs "Retry merge routing",
        then only the eligible one is routed and the counts are shown."""
        # given
        owner = merge_owner(21)
        eligible = create_merge_request(self.author, 1)
        assigned = create_merge_request(self.author, 2)
        MergeAssignment.objects.create(
            merge_request=assigned,
            merge_owner=owner,
            window=RETRY_TIME + timedelta(hours=2),
            reason="earlier",
        )
        in_review = create_merge_request(self.author, 3, MergeRequestState.IN_REVIEW)

        # when
        with at(RETRY_TIME):
            response = self.client.post(
                "/admin/reviews/mergerequest/",
                {
                    "action": "retry_merge_routing",
                    "_selected_action": [eligible.pk, assigned.pk, in_review.pk],
                },
                follow=True,
            )

        # then
        self.assertContains(response, "1 assigned, 0 still unassigned, 2 skipped.")
        self.assertEqual(
            MergeAssignment.objects.filter(merge_request=eligible).count(), 1
        )
        self.assertFalse(MergeAssignment.objects.filter(merge_request=in_review))
        self.assertEqual(
            AuditEvent.objects.get(action="merge.retry_run").after["trigger"], "admin"
        )
        self.assertFalse(
            [text for text in notify_texts() if "still have no merge owner" in text]
        )

    def test_mrdr_6_admin_rejects_an_invalid_daily_retry_time(self) -> None:
        """MRDR-6: Given the merge routing settings form, when the daily retry
        time is not a valid time, then it is rejected and nothing is saved."""
        # given
        settings_row = MergeRoutingSettings.load()
        url = f"/admin/reviews/mergeroutingsettings/{settings_row.pk}/change/"
        data = {"default_daily_merge_limit": "5", "merge_window_minutes": "30"}

        # when / then
        for value in ("25:00", "8:61", "eight"):
            with self.subTest(value=value):
                response = self.client.post(url, {**data, "daily_retry_time": value})

                self.assertContains(response, "errornote")
                settings_row.refresh_from_db()
                self.assertEqual(settings_row.daily_retry_time, time(8, 0))
        with self.subTest(value="09:30"):
            response = self.client.post(url, {**data, "daily_retry_time": "09:30"})

            self.assertEqual(response.status_code, 302)
            settings_row.refresh_from_db()
            self.assertEqual(settings_row.daily_retry_time, time(9, 30))
