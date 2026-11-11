# pylint: disable=duplicate-code

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from audit.models import AuditEvent
from integrations.models import OutboxCall
from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews import urgency, watchdog
from reviews.models import (
    MergeRequest,
    MergeRequestState,
    Person,
    RebookingSettings,
    Team,
    UrgencyRequest,
    UrgencyRequestStatus,
)
from reviews.tests.test_urgency import pending_request, wait_until_blocked_on_locks
from reviews.tests.test_worker_guard import idle_loop

TEHRAN = ZoneInfo("Asia/Tehran")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A Tehran time in November 2026. The 9th is a Monday, the 12th a Thursday."""
    return datetime(2026, 11, day, hour, minute, tzinfo=TEHRAN)


def notifications() -> list[tuple[list[int], str]]:
    return [
        (call.payload["gitlab_user_ids"], call.payload["text"])
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def audited(action: str) -> int:
    return AuditEvent.objects.filter(action=action).count()


def set_thresholds(reminder: int, escalation: int | None) -> None:
    RebookingSettings.objects.update_or_create(
        pk=1,
        defaults={
            "urgency_reminder_hours": reminder,
            "urgency_escalation_hours": escalation,
        },
    )


def waiting(
    iid: int, author: Person, approver: Person | None, since: datetime
) -> UrgencyRequest:
    """A pending request whose approver was assigned at `since`."""
    merge_request = create_merge_request(
        author, iid, f"MR {iid}", MergeRequestState.IN_REVIEW
    )
    request = pending_request(merge_request, author, approver)
    UrgencyRequest.objects.filter(pk=request.pk).update(approver_since=since)
    request.refresh_from_db()
    return request


class UrgencyCheckTests(TestCase):
    def setUp(self) -> None:
        self.director = create_person(10, "Dina Director", "dina")
        self.boss = create_person(1, "Bob Boss", "boss")
        self.boss.reports_to = self.director
        self.boss.save()
        self.author = create_person(2, "Ann Author", "ann")
        self.author.reports_to = self.boss
        self.author.save()
        self.admin = create_person(3, "Ada Admin", "ada")
        self.admin.is_admin = True
        self.admin.save()
        set_thresholds(reminder=4, escalation=None)

    def test_ugf_4_a_waiting_approver_is_reminded_once(self):
        """UGF-4: Given a pending request, when its approver has waited past the
        reminder threshold in company working hours, then the approver (or the
        admins) is reminded once with urgency.reminded; hours on days the
        company does not work do not count."""
        # given
        mine = waiting(1, self.author, self.boss, at(9, 8))
        adrift = waiting(2, self.author, None, at(9, 8))
        fresh = waiting(3, self.author, self.boss, at(13, 0))
        # Wednesday 22:00 to Friday 23:00 holds two company hours (Wednesday).
        over_thursday = waiting(4, self.author, self.boss, at(11, 22))

        # when
        urgency.run_urgency_check(at(9, 13))
        urgency.run_urgency_check(at(9, 14))
        urgency.run_urgency_check(at(13, 23))

        # then
        with self.subTest("once, with the hours as an integer"):
            reminders = [
                text
                for ids, text in notifications()
                if ids == [self.boss.gitlab_user_id]
            ]
            self.assertEqual(
                reminders,
                [
                    "Reminder: urgency for MR 7:1 by Ann Author has waited "
                    "5 hours. Decide in the panel."
                ],
            )
            mine.refresh_from_db()
            self.assertEqual(mine.reminded_at, at(9, 13))
            self.assertEqual(audited("urgency.reminded"), 2)
        with self.subTest("no approver: every admin"):
            self.assertIn(
                (
                    [self.admin.gitlab_user_id],
                    "Reminder: urgency for MR 7:2 by Ann Author has waited 5 hours. "
                    "Decide in the panel.",
                ),
                notifications(),
            )
            adrift.refresh_from_db()
            self.assertIsNotNone(adrift.reminded_at)
        with self.subTest("under the threshold, or a weekend gap: nothing"):
            fresh.refresh_from_db()
            over_thursday.refresh_from_db()
            self.assertIsNone(fresh.reminded_at)
            self.assertIsNone(over_thursday.reminded_at)
        with self.subTest(
            "one failing request: the rest are reminded, then the check raises"
        ):
            first = waiting(10, self.author, self.boss, at(9, 1))
            second = waiting(11, self.author, self.boss, at(9, 1))
            real_notify = urgency._notify_approver  # pylint: disable=protected-access

            def notify(request, text):
                if request.pk == first.pk:
                    raise RuntimeError("chat down")
                real_notify(request, text)

            with (
                mock.patch.object(urgency, "_notify_approver", side_effect=notify),
                self.assertLogs("reviews.urgency", "ERROR") as logs,
                self.assertRaisesMessage(
                    RuntimeError, f"urgency check failed for requests [{first.pk}]"
                ),
            ):
                urgency.run_urgency_check(at(9, 13))

            first.refresh_from_db()
            second.refresh_from_db()
            self.assertIsNone(first.reminded_at)
            self.assertIsNotNone(second.reminded_at)
            self.assertIn(f"request {first.pk}", logs.output[0])
            self.assertEqual(
                AuditEvent.objects.filter(
                    action="urgency.reminded", subject_id=str(first.pk)
                ).count(),
                0,
            )

    def test_ugf_5_a_long_wait_moves_the_request_one_level_up(
        self,
    ):  # pylint: disable=too-many-statements
        """UGF-5: Given a pending request past the escalation threshold, when
        the check runs, then it moves to the approver resolved from the current
        approver's own manager (never the author, the admins when nobody is
        left), both are told, urgency.escalated is written and the clocks
        restart; an empty threshold never escalates."""
        # given
        set_thresholds(reminder=1, escalation=2)
        mine = waiting(1, self.author, self.boss, at(9, 8))

        # when
        urgency.run_urgency_check(at(9, 10, 30))
        urgency.run_urgency_check(at(9, 10, 30))

        # then
        with self.subTest("one level up, both told, clocks restarted"):
            mine.refresh_from_db()
            self.assertEqual(mine.approver, self.director)
            self.assertEqual(mine.approver_since, at(9, 10, 30))
            self.assertIsNone(mine.reminded_at)
            self.assertEqual(audited("urgency.escalated"), 1)
            self.assertEqual(
                notifications()[-2:],
                [
                    ([1], "The urgency request for MR 7:1 was moved to Dina Director."),
                    (
                        [10],
                        "Urgency requested for MR 7:1 by Ann Author was escalated "
                        "to you: Release blocker. Decide in the panel.",
                    ),
                ],
            )
        with self.subTest("no second escalation before the threshold passes again"):
            urgency.run_urgency_check(at(9, 11, 45))

            mine.refresh_from_db()
            self.assertEqual(mine.approver, self.director)
            self.assertEqual(mine.reminded_at, at(9, 11, 45))
            self.assertEqual(audited("urgency.escalated"), 1)
        with self.subTest("nobody above: the admins, and only reminders after"):
            urgency.run_urgency_check(at(9, 13, 45))

            mine.refresh_from_db()
            self.assertIsNone(mine.approver)
            self.assertEqual(notifications()[-1][0], [self.admin.gitlab_user_id])
            urgency.run_urgency_check(at(9, 15, 45))
            urgency.run_urgency_check(at(9, 20))
            mine.refresh_from_db()
            self.assertIsNone(mine.approver)
            self.assertIsNotNone(mine.reminded_at)
            self.assertEqual(audited("urgency.escalated"), 2)
        with self.subTest("the manager's manager is off: their delegate"):
            delegate = create_person(11, "Dan Delegate", "dan")
            Team.objects.create(name="Web", lead=self.director, delegate=delegate)
            self.director.working_days = [5, 6, 1, 2]
            self.director.save()
            second = waiting(2, self.author, self.boss, at(9, 8))

            urgency.run_urgency_check(at(9, 10, 30))

            second.refresh_from_db()
            self.assertEqual(second.approver, delegate)
        with self.subTest("never the requester: a loop ends at the admins"):
            looped = create_person(20, "Lou Looped", "lou")
            lead = create_person(21, "Lee Lead", "lee")
            looped.reports_to = lead
            looped.save()
            lead.reports_to = looped
            lead.save()
            third = waiting(3, looped, lead, at(9, 8))

            urgency.run_urgency_check(at(9, 10, 30))

            third.refresh_from_db()
            self.assertIsNone(third.approver)
        with self.subTest("an empty escalation threshold never escalates"):
            set_thresholds(reminder=1, escalation=None)
            fourth = waiting(4, self.author, self.boss, at(9, 0))

            urgency.run_urgency_check(at(9, 23))

            fourth.refresh_from_db()
            self.assertEqual(fourth.approver, self.boss)
            self.assertIsNotNone(fourth.reminded_at)

    def test_ugf_8_watchdog_lists_requests_pending_past_the_reminder(self):
        """UGF-8: Given pending requests, when the watchdog runs, then those
        waiting past the reminder threshold are listed under "Urgency pending"
        with the author, the approver (or the admins) and the hours, and one
        under the threshold is not."""
        # given
        now = at(4, 8)
        set_thresholds(reminder=4, escalation=8)
        RebookingSettings.objects.filter(pk=1).update(last_rebooking_on=now.date())
        waiting(1, self.author, self.boss, at(4, 2))
        waiting(2, self.author, self.boss, at(4, 6))
        waiting(3, self.author, None, at(4, 2))

        # when
        counts = watchdog.run_daily_watchdog(now)

        # then
        self.assertEqual(counts["urgency_pending"], 2)
        summary = notifications()[-1][1]
        self.assertIn("Urgency pending (2):", summary)
        self.assertIn("7:1 MR 1 — Ann Author → Bob Boss — 6 hours", summary)
        self.assertIn("7:3 MR 3 — Ann Author → admins — 6 hours", summary)
        self.assertNotIn("7:2 MR 2", summary)


class UrgencyCheckConcurrencyTests(TransactionTestCase):
    def setUp(self) -> None:
        self.director = create_person(10, "Dina Director", "dina")
        self.boss = create_person(1, "Bob Boss", "boss")
        self.boss.reports_to = self.director
        self.boss.save()
        self.author = create_person(2, "Ann Author", "ann")
        self.author.reports_to = self.boss
        self.author.save()
        set_thresholds(reminder=1, escalation=4)

    def hold_lock(self, merge_request, locked, release) -> None:
        try:
            with transaction.atomic():
                MergeRequest.objects.select_for_update().get(pk=merge_request.pk)
                locked.set()
                release.wait(10)
        finally:
            connection.close()

    def run_check(self) -> None:
        try:
            urgency.run_urgency_check(at(9, 15))
        finally:
            connection.close()

    def approve_as_boss(self, pk: int) -> str:
        try:
            urgency.approve(self.boss, pk, "", "user:boss")
            return "approved"
        except UrgencyRequest.DoesNotExist:
            return "does not exist"
        finally:
            connection.close()

    def test_ugf_6_two_checks_act_once(self):  # pylint: disable=too-many-locals
        """UGF-6: Given a request past a threshold, when two checks run at the
        same moment and wait for the MR lock, then it is reminded once, or
        escalated once."""
        cases = [
            ("remind", at(9, 13), 1, 0),
            ("escalate", at(9, 8), 1, 1),
        ]
        for iid, (label, since, reminded, escalated) in enumerate(cases, start=1):
            with self.subTest(label):
                # given
                request = waiting(iid, self.author, self.boss, since)
                locked, release = threading.Event(), threading.Event()

                # when
                with ThreadPoolExecutor(max_workers=3) as pool:
                    holding = pool.submit(
                        self.hold_lock, request.merge_request, locked, release
                    )
                    locked.wait(5)
                    checks = [pool.submit(self.run_check) for _ in range(2)]
                    try:
                        wait_until_blocked_on_locks(2)
                    finally:
                        release.set()
                    holding.result(timeout=10)
                    for check in checks:
                        check.result(timeout=10)

                # then
                # Audit events are never deleted, so the second case adds to the first.
                self.assertEqual(audited("urgency.reminded"), reminded)
                self.assertEqual(audited("urgency.escalated"), escalated)

        with self.subTest("the worker runs the check through the guard"):
            failing = mock.Mock(side_effect=RuntimeError("boom"))
            process = mock.Mock(return_value=False)

            with (
                idle_loop(run_urgency_check=failing, process_next_event=process),
                self.assertLogs("reviews.management.commands.run_worker", "ERROR"),
            ):
                call_command("run_worker", once=True)

            failing.assert_called_once_with()
            process.assert_called_once()
            self.assertIn(
                "Worker job run_urgency_check failed: RuntimeError: boom. "
                "It is skipped until its next run; see the worker log.",
                [text for _, text in notifications()],
            )

    def test_ugf_7_escalation_and_the_old_approvers_decision_apply_once(self):
        """UGF-7: Given a request past the escalation threshold, when the check
        and the old approver's approval wait for the MR lock, then exactly one
        applies: after an escalation the old approver gets DoesNotExist and the
        new approver can approve; after an approval the check does nothing."""
        orders = {
            "escalation first": (self.run_check, self.approve_as_boss),
            "approval first": (self.approve_as_boss, self.run_check),
        }
        for iid, (label, (first, second)) in enumerate(orders.items(), start=1):
            with self.subTest(label):
                # given
                request = waiting(iid, self.author, self.boss, at(9, 8))
                locked, release = threading.Event(), threading.Event()
                arguments = {self.approve_as_boss: (request.pk,), self.run_check: ()}

                # when
                with ThreadPoolExecutor(max_workers=3) as pool:
                    holding = pool.submit(
                        self.hold_lock, request.merge_request, locked, release
                    )
                    locked.wait(5)
                    queued = [pool.submit(first, *arguments[first])]
                    wait_until_blocked_on_locks(1)
                    queued.append(pool.submit(second, *arguments[second]))
                    try:
                        wait_until_blocked_on_locks(2)
                    finally:
                        release.set()
                    holding.result(timeout=10)
                    outcomes = [item.result(timeout=10) for item in queued]

                # then
                request.refresh_from_db()
                if label == "escalation first":
                    self.assertEqual(outcomes, [None, "does not exist"])
                    self.assertEqual(request.status, UrgencyRequestStatus.PENDING)
                    self.assertEqual(request.approver, self.director)
                    urgency.approve(self.director, request.pk, "", "user:dina")
                    request.refresh_from_db()
                else:
                    self.assertEqual(outcomes, ["approved", None])
                    self.assertEqual(request.approver, self.boss)
                self.assertEqual(request.status, UrgencyRequestStatus.APPROVED)
