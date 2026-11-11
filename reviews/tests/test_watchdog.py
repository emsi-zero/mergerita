# pylint: disable=duplicate-code

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import connection
from django.forms.models import model_to_dict
from django.test import TestCase, TransactionTestCase

from audit.models import AuditEvent
from integrations.models import OutboxCall
from reviews import watchdog
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    Person,
    RebookingSettings,
    ReviewSession,
)

TEHRAN = ZoneInfo("Asia/Tehran")
# A Wednesday. The company working days are Sat, Sun, Mon, Tue and Wed.
NOW = datetime(2026, 11, 4, 8, 0, tzinfo=TEHRAN)
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def local(month: int, day: int, hour: int = 10) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=TEHRAN)


def person(gitlab_user_id: int, **fields: object) -> Person:
    fields.setdefault("working_days", EVERY_DAY)
    return Person.objects.create(
        gitlab_user_id=gitlab_user_id, name=f"P{gitlab_user_id}", **fields
    )


def stuck_mr(
    author: Person,
    iid: int,
    state: str,
    since: datetime,
    **fields: object,
) -> MergeRequest:
    """Create an MR whose state and AI gate both date from `since`."""
    return MergeRequest.objects.create(
        project_id=7,
        iid=iid,
        author=author,
        title=f"MR {iid}",
        state=state,
        state_changed_at=since,
        ai_gate_entered_at=since,
        **fields,
    )


def booked_session(
    merge_request: MergeRequest, reviewer: Person, day: date
) -> ReviewSession:
    area_review = AreaReview.objects.create(
        merge_request=merge_request, state=AreaReviewState.BOOKED
    )
    return ReviewSession.objects.create(
        area_review=area_review, reviewer=reviewer, day=day, slot_minutes=30
    )


def claim_rebooking(day: date = NOW.date()) -> None:
    RebookingSettings.objects.update_or_create(
        pk=1, defaults={"last_rebooking_on": day, "last_watchdog_on": None}
    )


def notify_texts() -> list[str]:
    return [
        call.payload["text"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def rows() -> list[dict]:
    return [
        model_to_dict(row)
        for model in (MergeRequest, AreaReview, ReviewSession, MergeAssignment)
        for row in model.objects.order_by("pk")
    ]


class WatchdogCasesTest(TestCase):
    def setUp(self) -> None:
        self.author = person(11)
        claim_rebooking()

    def test_rbw_13_reports_an_mr_with_no_ai_verdict_after_the_threshold(self) -> None:
        """RBW-13: Given an MR in awaiting_ai_review, when more than the
        threshold of working days passed since it entered the gate, then it is
        reported under "No AI verdict"; an MR within the threshold is not."""
        # given
        stuck_mr(self.author, 1, MergeRequestState.AWAITING_AI_REVIEW, local(11, 2))
        stuck_mr(self.author, 2, MergeRequestState.AWAITING_AI_REVIEW, local(11, 3))

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["no_ai_verdict"], 1)
        self.assertEqual(
            notify_texts(),
            ["Stuck MRs (1):\nNo AI verdict (1):\n7:1 MR 1 — 2 working day(s)"],
        )

    def test_rbw_14_reports_a_missed_slot_and_reminds_the_reviewer(self) -> None:
        """RBW-14: Given a booked area review with a pending session on a past
        day, when the watchdog runs, then it is reported under "Missed slot",
        the reviewer gets one chat, and no MR, area review, session or
        assignment changes."""
        # given
        reviewer = person(21)
        merge_request = stuck_mr(
            self.author, 1, MergeRequestState.IN_REVIEW, local(11, 3)
        )
        booked_session(merge_request, reviewer, date(2026, 11, 2))
        decoy = stuck_mr(self.author, 2, MergeRequestState.IN_REVIEW, local(11, 3))
        booked_session(decoy, reviewer, date(2026, 11, 4))
        before = rows()

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["missed_slot"], 1)
        chats = OutboxCall.objects.filter(kind="notify", payload__gitlab_user_ids=[21])
        self.assertEqual(
            [call.payload["text"] for call in chats],
            [
                "Your review of MR 7:1 was booked on 2026-11-02 and has no outcome. "
                "Mark it done, ask for more time, or hand it back in the panel."
            ],
        )
        self.assertEqual(rows(), before)
        with self.subTest("two missed sessions of one MR"):
            two = stuck_mr(self.author, 3, MergeRequestState.IN_REVIEW, local(11, 3))
            booked_session(two, reviewer, date(2026, 11, 2))
            booked_session(two, reviewer, date(2026, 11, 3))
            claim_rebooking()
            OutboxCall.objects.all().delete()

            counts = watchdog.run_daily_watchdog(NOW)

            self.assertEqual(counts["missed_slot"], 2)
            summary = notify_texts()[0]
            self.assertEqual(summary.count("7:3 MR 3"), 1)
            self.assertIn("7:3 MR 3 — 2 working day(s)", summary)
            self.assertEqual(len(notify_texts()), 4)

    def test_rbw_15_reports_a_triage_mr_after_the_threshold(self) -> None:
        """RBW-15: Given a needs_triage MR, when more than the triage threshold
        of working days passed since its state changed, then it is reported
        under "Needs triage"; a newer one is not."""
        # given
        stuck_mr(self.author, 1, MergeRequestState.NEEDS_TRIAGE, local(11, 2))
        stuck_mr(self.author, 2, MergeRequestState.NEEDS_TRIAGE, local(11, 3))

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["needs_triage"], 1)
        self.assertIn("Needs triage (1):\n7:1 MR 1", notify_texts()[0])

    def test_rbw_16_reports_other_stale_mrs_once_under_no_progress(self) -> None:
        """RBW-16: Given MRs with no state change for more than the stale
        threshold, when the watchdog runs, then an in_review MR and an approved
        MR with an open merge assignment are reported under "No progress"; a
        recent MR, a merged MR and an MR already listed under another case are
        not."""
        # given
        old = local(10, 25)
        stuck_mr(self.author, 1, MergeRequestState.IN_REVIEW, old)
        approved = stuck_mr(self.author, 2, MergeRequestState.APPROVED, old)
        MergeAssignment.objects.create(merge_request=approved)
        stuck_mr(self.author, 3, MergeRequestState.IN_REVIEW, local(11, 3))
        stuck_mr(self.author, 4, MergeRequestState.MERGED, old)
        stuck_mr(self.author, 5, MergeRequestState.NEEDS_TRIAGE, old)

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["no_progress"], 2)
        self.assertEqual(counts["needs_triage"], 1)
        self.assertEqual(
            notify_texts()[0],
            "Stuck MRs (3):\n"
            "Needs triage (1):\n7:5 MR 5 — 8 working day(s)\n"
            "No progress (2):\n7:1 MR 1 — 8 working day(s)\n"
            "7:2 MR 2 — 8 working day(s)",
        )

    def test_rbw_17_leaves_out_what_other_summaries_report(self) -> None:
        """RBW-17: Given an old MR with an unbooked area review, an old
        waiting_on_author MR and an old approved MR with no open assignment,
        when the watchdog runs, then none is reported and no alert is sent."""
        # given
        old = local(10, 25)
        unbooked = stuck_mr(self.author, 1, MergeRequestState.AWAITING_BOOKING, old)
        AreaReview.objects.create(merge_request=unbooked)
        stuck_mr(self.author, 2, MergeRequestState.WAITING_ON_AUTHOR, old)
        approved = stuck_mr(self.author, 3, MergeRequestState.APPROVED, old)
        MergeAssignment.objects.create(merge_request=approved, merged_at=old)

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(notify_texts(), [])

    def test_rbw_18_sends_one_grouped_alert_with_urls_and_audits_the_counts(
        self,
    ) -> None:
        """RBW-18: Given stuck MRs, when the watchdog runs, then the admins get
        one alert grouped by case (web_url only when known), one watchdog.run
        event holds the counts, and the MR is reported again the next day; with
        nothing stuck there is no alert."""
        # given
        person(1, is_admin=True)
        stuck_mr(
            self.author,
            1,
            MergeRequestState.AWAITING_AI_REVIEW,
            local(11, 2),
            web_url="https://gitlab.example/7/1",
        )
        stuck_mr(self.author, 2, MergeRequestState.NEEDS_TRIAGE, local(11, 2))

        # when
        watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(
            notify_texts(),
            [
                "Stuck MRs (2):\n"
                "No AI verdict (1):\n"
                "7:1 MR 1 https://gitlab.example/7/1 — 2 working day(s)\n"
                "Needs triage (1):\n7:2 MR 2 — 2 working day(s)"
            ],
        )
        self.assertEqual(OutboxCall.objects.get().payload["gitlab_user_ids"], [1])
        event = AuditEvent.objects.get(action="watchdog.run")
        self.assertEqual(
            event.after,
            {
                "no_ai_verdict": 1,
                "missed_slot": 0,
                "needs_triage": 1,
                "no_progress": 0,
                "urgency_pending": 0,
            },
        )
        with self.subTest("still stuck the next day"):
            tomorrow = NOW + timedelta(days=1)
            claim_rebooking(tomorrow.date())

            watchdog.run_daily_watchdog(tomorrow)

            self.assertEqual(len(notify_texts()), 2)
        with self.subTest("nothing stuck"):
            MergeRequest.objects.update(state=MergeRequestState.MERGED)
            later = NOW + timedelta(days=2)
            claim_rebooking(later.date())

            watchdog.run_daily_watchdog(later)

            self.assertEqual(len(notify_texts()), 2)
            self.assertEqual(
                AuditEvent.objects.filter(action="watchdog.run").count(), 3
            )

    def test_rbw_19_counts_working_days_in_tehran(self) -> None:
        """RBW-19: Given a local midnight boundary, a weekend, and a reviewer
        with their own working days, when the watchdog counts, then it uses the
        Tehran date, the company days for MRs and the reviewer's days for a
        missed slot."""
        with self.subTest("midnight: 00:30 Tehran is still Monday in UTC"):
            # given: Tuesday 00:30 local; only Wednesday has passed
            stuck_mr(
                self.author,
                1,
                MergeRequestState.AWAITING_AI_REVIEW,
                datetime(2026, 11, 3, 0, 30, tzinfo=TEHRAN),
            )

            # when
            counts = watchdog.run_daily_watchdog(NOW)

            # then
            self.assertEqual(counts["no_ai_verdict"], 0)
        with self.subTest("weekend: Thursday and Friday do not count"):
            # given: three calendar days, one working day
            friday = datetime(2026, 11, 6, 8, 0, tzinfo=TEHRAN)
            claim_rebooking(friday.date())

            # when
            counts = watchdog.run_daily_watchdog(friday)

            # then
            self.assertEqual(counts["no_ai_verdict"], 0)
        with self.subTest("a missed slot uses the reviewer's working days"):
            # given: Mon and Tue count for the reviewer, Mon to Wed for the company
            reviewer = person(21, working_days=[0, 1])
            merge_request = stuck_mr(
                self.author, 2, MergeRequestState.IN_REVIEW, local(11, 4)
            )
            booked_session(merge_request, reviewer, date(2026, 11, 1))
            claim_rebooking()

            # when
            watchdog.run_daily_watchdog(NOW)

            # then
            self.assertIn("7:2 MR 2 — 2 working day(s)", notify_texts()[0])


class WatchdogConcurrencyTest(TransactionTestCase):
    def test_rbw_12_runs_once_per_day_and_only_after_the_rebooking(self) -> None:
        """RBW-12: Given the run time has passed, when the day is not claimed
        for rebooking, then the watchdog does not run; when it is, two workers
        reaching the run time run it once, and a later pass the same day does
        nothing."""
        # given
        author = person(11)
        stuck_mr(author, 1, MergeRequestState.NEEDS_TRIAGE, local(11, 2))
        with self.subTest("not before the rebooking claim"):
            self.assertIsNone(watchdog.run_daily_watchdog(NOW))
            claim_rebooking(NOW.date() - timedelta(days=1))
            self.assertIsNone(watchdog.run_daily_watchdog(NOW))
        with self.subTest("not before the run time"):
            claim_rebooking()
            early = datetime(2026, 11, 4, 7, 0, tzinfo=TEHRAN)
            self.assertIsNone(watchdog.run_daily_watchdog(early))
        claim_rebooking()
        barrier = threading.Barrier(2)

        def run(_: int) -> dict[str, int] | None:
            try:
                barrier.wait()
                return watchdog.run_daily_watchdog(NOW)
            finally:
                connection.close()

        # when
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))

        # then
        self.assertEqual([result is None for result in results].count(True), 1)
        self.assertEqual(AuditEvent.objects.filter(action="watchdog.run").count(), 1)
        self.assertEqual(len(notify_texts()), 1)
        with self.subTest("a second pass the same day"):
            self.assertIsNone(watchdog.run_daily_watchdog(NOW + timedelta(hours=8)))


class NoProgressWaitingTest(TestCase):
    """RWF: an MR waiting for a booked future slot is not stuck."""

    def setUp(self) -> None:
        self.author = person(11)
        self.reviewer = person(21)
        claim_rebooking()

    def test_rwf_1_no_progress_skips_an_mr_with_a_session_today_or_later(
        self,
    ) -> None:
        """RWF-1: Given in_review MRs unchanged for weeks, when one has a
        pending session today and one on a later day, then neither is reported
        under "No progress"; an MR with no session still is."""
        # given
        today_mr = stuck_mr(self.author, 1, MergeRequestState.IN_REVIEW, local(10, 20))
        later_mr = stuck_mr(self.author, 2, MergeRequestState.IN_REVIEW, local(10, 20))
        stuck_mr(self.author, 3, MergeRequestState.IN_REVIEW, local(10, 20))
        booked_session(today_mr, self.reviewer, NOW.date())
        booked_session(later_mr, self.reviewer, date(2026, 11, 7))

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["no_progress"], 1)
        self.assertEqual(counts["missed_slot"], 0)
        self.assertIn("No progress (1):\n7:3 MR 3", notify_texts()[0])

    def test_rwf_2_no_progress_skips_an_mr_with_a_merge_window_now_or_later(
        self,
    ) -> None:
        """RWF-2: Given approved MRs unchanged for weeks, when one has an open
        merge assignment with a window later today, then it is not reported
        under "No progress"; one whose window has passed still is."""
        # given
        waiting = stuck_mr(self.author, 1, MergeRequestState.APPROVED, local(10, 20))
        missed = stuck_mr(self.author, 2, MergeRequestState.APPROVED, local(10, 20))
        MergeAssignment.objects.create(
            merge_request=waiting, window=NOW + timedelta(hours=2)
        )
        MergeAssignment.objects.create(
            merge_request=missed, window=NOW - timedelta(days=1)
        )

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["no_progress"], 1)
        self.assertIn("No progress (1):\n7:2 MR 2", notify_texts()[0])

    def test_rwf_3_a_past_only_session_is_a_missed_slot_not_no_progress(
        self,
    ) -> None:
        """RWF-3: Given an in_review MR unchanged for weeks whose only pending
        session was on a past day, when the watchdog runs, then it is reported
        under "Missed slot" and not under "No progress"."""
        # given
        merge_request = stuck_mr(
            self.author, 1, MergeRequestState.IN_REVIEW, local(10, 20)
        )
        booked_session(merge_request, self.reviewer, date(2026, 11, 2))

        # when
        counts = watchdog.run_daily_watchdog(NOW)

        # then
        self.assertEqual(counts["missed_slot"], 1)
        self.assertEqual(counts["no_progress"], 0)
        self.assertNotIn("No progress", notify_texts()[0])
