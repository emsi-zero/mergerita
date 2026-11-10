from datetime import time
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from integrations.models import OutboxCall
from reviews import watchdog
from reviews.management.commands import run_worker
from reviews.models import RebookingSettings

LOGGER = "reviews.management.commands.run_worker"


def alerts() -> list[str]:
    return [
        call.payload["text"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def idle_loop(**patches: object):
    """Patch the worker loop's calls: idle inbox and outbox, no-op jobs."""
    defaults = {
        "reconcile_all": mock.Mock(),
        "run_daily_retry": mock.Mock(),
        "run_daily_rebooking": mock.Mock(),
        "run_daily_watchdog": mock.Mock(),
        "process_next_event": mock.Mock(return_value=False),
        "dispatch_due": mock.Mock(return_value=False),
    }
    return mock.patch.multiple(run_worker, **(defaults | patches))


class WorkerGuardTest(TestCase):
    def test_rwf_4_a_failing_job_is_logged_alerted_and_the_loop_goes_on(
        self,
    ) -> None:
        """RWF-4: Given the watchdog raises, when the worker runs a pass, then
        the exception is logged, one admin alert is queued, and the pass still
        processes the inbox and dispatches the outbox."""
        # given
        failing = mock.Mock(side_effect=RuntimeError("boom"))
        process = mock.Mock(return_value=False)
        dispatch = mock.Mock(return_value=False)

        # when
        with (
            idle_loop(
                run_daily_watchdog=failing,
                process_next_event=process,
                dispatch_due=dispatch,
            ),
            self.assertLogs(LOGGER, "ERROR") as logs,
        ):
            call_command("run_worker", once=True)

        # then
        process.assert_called_once()
        dispatch.assert_called_once()
        self.assertIn("worker job run_daily_watchdog failed", logs.output[0])
        self.assertEqual(
            alerts(),
            [
                "Worker job run_daily_watchdog failed: RuntimeError: boom. "
                "It is skipped until its next run; see the worker log."
            ],
        )

    def test_rwf_5_a_failing_streak_alerts_once_until_the_job_succeeds(
        self,
    ) -> None:
        """RWF-5: Given a job that raises on three passes, then succeeds, then
        raises again, when the guard runs it each time, then two alerts are
        queued: one per failing streak."""
        # given
        failing: set[str] = set()
        job = mock.Mock(
            side_effect=[
                RuntimeError("down"),
                RuntimeError("down"),
                RuntimeError("down"),
                None,
                RuntimeError("down again"),
            ]
        )

        # when
        with self.assertLogs(LOGGER, "ERROR") as logs:
            for _ in range(5):
                run_worker.run_guarded("reconcile_all", job, failing)

        # then: a traceback for the first failure of each streak, one line after
        self.assertEqual(
            [record.exc_info is not None for record in logs.records],
            [True, False, False, True],
        )
        self.assertEqual(
            alerts(),
            [
                "Worker job reconcile_all failed: RuntimeError: down. "
                "It is skipped until its next run; see the worker log.",
                "Worker job reconcile_all failed: RuntimeError: down again. "
                "It is skipped until its next run; see the worker log.",
            ],
        )

    def test_rwf_6_a_daily_job_that_raises_after_its_claim_is_not_retried_that_day(
        self,
    ) -> None:
        """RWF-6: Given the watchdog's report raises after it claimed the day,
        when the worker runs two passes on the same day, then the report is
        tried once and the day stays claimed."""
        # given
        RebookingSettings.objects.update_or_create(
            pk=1, defaults={"daily_run_time": time(0, 0)}
        )
        report = mock.Mock(side_effect=RuntimeError("boom"))

        # when
        with (
            idle_loop(
                run_daily_rebooking=run_worker.run_daily_rebooking,
                run_daily_watchdog=run_worker.run_daily_watchdog,
            ),
            mock.patch.object(watchdog, "_report", report),
            self.assertLogs(LOGGER, "ERROR"),
        ):
            call_command("run_worker", once=True)
            call_command("run_worker", once=True)

        # then
        report.assert_called_once()
        self.assertEqual(
            RebookingSettings.load().last_watchdog_on, timezone.localdate()
        )
