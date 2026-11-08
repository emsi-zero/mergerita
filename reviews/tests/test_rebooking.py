# pylint: disable=duplicate-code

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from audit.models import AuditEvent
from integrations.fakes import FakeGitLabGateway
from integrations.models import OutboxCall, OutboxStatus
from reviews import area_resolver, booking, rebooking
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
    RebookingSettings,
    ReviewSession,
    ReviewSessionOutcome,
)
from reviews.tests.test_merge_retry import wait_until_blocked_on_a_lock

TEHRAN = ZoneInfo("Asia/Tehran")
RUN_TIME = datetime(2026, 11, 4, 8, 0, tzinfo=TEHRAN)  # a Wednesday, run time 07:30
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def at(when: datetime):
    return mock.patch("django.utils.timezone.now", return_value=when)


def person(gitlab_user_id: int, **fields: object) -> Person:
    fields.setdefault("working_days", EVERY_DAY)
    return Person.objects.create(
        gitlab_user_id=gitlab_user_id, name=f"P{gitlab_user_id}", **fields
    )


def unbooked_review(
    author: Person,
    iid: int,
    *owners: tuple[Person, str],
    state: str = MergeRequestState.AWAITING_BOOKING,
    waiting_since: datetime | None = None,
    area: CodeArea | None = None,
) -> AreaReview:
    """Create an MR with one unbooked area review of an area owned by `owners`."""
    if area is None:
        area = CodeArea.objects.create(
            name=f"area{iid}", path_patterns=[], size_limit_lines=100
        )
    for owner, role in owners:
        Ownership.objects.create(person=owner, area=area, role=role)
    merge_request = MergeRequest.objects.create(
        project_id=7, iid=iid, author=author, title=f"MR {iid}", state=state
    )
    area_review = AreaReview.objects.create(
        merge_request=merge_request, unbooked_since=waiting_since or RUN_TIME
    )
    area_review.areas.set([area])
    return area_review


def notify_texts() -> list[str]:
    return [
        call.payload["text"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def summaries() -> list[str]:
    return [text for text in notify_texts() if "still have no reviewer" in text]


class DailyRebookingTest(TestCase):
    """The daily rebooking of unbooked area reviews."""

    def setUp(self) -> None:
        self.author = person(11)
        person(99, is_admin=True)

    def test_rbw_1_rebooking_runs_at_the_run_time_and_books_open_mrs_only(
        self,
    ) -> None:
        """RBW-1: Given unbooked area reviews of MRs in awaiting_booking,
        in_review and needs_triage and an owner who now fits, when the local
        time reaches the run time, then the first two are booked and the third
        is not; before that time nothing runs."""
        # given
        owner = person(21)
        first = unbooked_review(self.author, 1, (owner, OwnerRole.PRIMARY))
        second = unbooked_review(
            self.author,
            2,
            (owner, OwnerRole.PRIMARY),
            state=MergeRequestState.IN_REVIEW,
        )
        triage = unbooked_review(
            self.author,
            3,
            (owner, OwnerRole.PRIMARY),
            state=MergeRequestState.NEEDS_TRIAGE,
        )

        # when / then
        with self.subTest("before the run time"):
            with at(RUN_TIME - timedelta(minutes=45)):
                self.assertIsNone(rebooking.run_daily_rebooking())

            first.refresh_from_db()
            self.assertEqual(first.state, AreaReviewState.UNBOOKED)
        with self.subTest("at the run time"):
            with at(RUN_TIME):
                result = rebooking.run_daily_rebooking()

            self.assertEqual(result.booked, 2)
            for area_review in (first, second, triage):
                area_review.refresh_from_db()
            self.assertEqual(first.reviewer, owner)
            self.assertEqual(second.reviewer, owner)
            self.assertEqual(triage.state, AreaReviewState.UNBOOKED)
            self.assertEqual(
                RebookingSettings.load().last_rebooking_on, RUN_TIME.date()
            )

    def test_rbw_2_oldest_wait_takes_the_first_free_slot(self) -> None:
        """RBW-2: Given two unbooked area reviews and an owner with room for one
        slot a day, when the rebooking runs, then the one that waited longer gets
        the earlier day, although it was created later."""
        # given
        owner = person(21, daily_budget_minutes=60)
        area = CodeArea.objects.create(
            name="shared", path_patterns=[], size_limit_lines=1
        )
        Ownership.objects.create(person=owner, area=area, role=OwnerRole.PRIMARY)
        newer = unbooked_review(
            self.author, 1, area=area, waiting_since=RUN_TIME - timedelta(days=1)
        )
        older = unbooked_review(
            self.author, 2, area=area, waiting_since=RUN_TIME - timedelta(days=8)
        )

        # when
        with at(RUN_TIME):
            rebooking.run_daily_rebooking()

        # then
        newer.refresh_from_db()
        older.refresh_from_db()
        self.assertEqual(older.booked_day, RUN_TIME.date())
        self.assertGreater(newer.booked_day, older.booked_day)

    def test_rbw_4_previous_reviewer_is_tried_first_as_a_continuation(self) -> None:
        """RBW-4: Given an area review with an earlier session by a backup owner
        and a primary owner who would win on tier, when it is rebooked, then the
        earlier reviewer gets a continuation session; when that reviewer has no
        free slot in the horizon, another eligible owner is booked."""
        # given
        previous = person(21, daily_budget_minutes=60)
        other = person(22)
        area_review = unbooked_review(
            self.author, 1, (previous, OwnerRole.BACKUP), (other, OwnerRole.PRIMARY)
        )
        ReviewSession.objects.create(
            area_review=area_review,
            reviewer=previous,
            day=RUN_TIME.date() - timedelta(days=2),
            slot_minutes=30,
            outcome=ReviewSessionOutcome.MORE_TIME,
        )

        # when
        with at(RUN_TIME):
            rebooking.retry_unbooked(rebooking.ADMIN)

        # then
        session = area_review.sessions.get(outcome=ReviewSessionOutcome.PENDING)
        self.assertEqual(session.reviewer, previous)
        self.assertTrue(session.is_continuation)
        with self.subTest("the previous reviewer has no free slot"):
            booking_days = [RUN_TIME.date() + timedelta(days=n) for n in range(10)]
            area_review.sessions.filter(outcome=ReviewSessionOutcome.PENDING).update(
                outcome=ReviewSessionOutcome.SUPERSEDED
            )
            AreaReview.objects.filter(pk=area_review.pk).update(
                state=AreaReviewState.UNBOOKED, reviewer=None, booked_day=None
            )
            for day in booking_days:
                CapacityDay.objects.update_or_create(
                    person=previous,
                    day=day,
                    defaults={"budget_minutes": 60, "booked_minutes": 60},
                )

            with at(RUN_TIME):
                rebooking.retry_unbooked(rebooking.ADMIN)

            latest = area_review.sessions.get(outcome=ReviewSessionOutcome.PENDING)
            self.assertEqual(latest.reviewer, other)
            self.assertTrue(latest.is_continuation)

    def test_rbw_5_a_person_who_handed_back_is_never_booked_again(self) -> None:
        """RBW-5: Given an area review that a person handed back in an earlier
        session, when it is rebooked, then that person is not booked, not even
        when they are the only owner."""
        # given
        handed_back = person(21)
        other = person(22)
        area_review = unbooked_review(
            self.author, 1, (handed_back, OwnerRole.PRIMARY), (other, OwnerRole.BACKUP)
        )
        ReviewSession.objects.create(
            area_review=area_review,
            reviewer=handed_back,
            day=RUN_TIME.date() - timedelta(days=2),
            slot_minutes=30,
            outcome=ReviewSessionOutcome.HANDED_BACK,
        )

        # when
        with at(RUN_TIME):
            rebooking.retry_unbooked(rebooking.ADMIN)

        # then
        area_review.refresh_from_db()
        self.assertEqual(area_review.reviewer, other)
        with self.subTest("the only owner"):
            only = unbooked_review(self.author, 2, (handed_back, OwnerRole.PRIMARY))
            ReviewSession.objects.create(
                area_review=only,
                reviewer=handed_back,
                day=RUN_TIME.date() - timedelta(days=2),
                slot_minutes=30,
                outcome=ReviewSessionOutcome.HANDED_BACK,
            )

            with at(RUN_TIME):
                result = rebooking.retry_unbooked(rebooking.ADMIN)

            only.refresh_from_db()
            self.assertEqual(only.state, AreaReviewState.UNBOOKED)
            self.assertEqual(result.booked, 0)

    def test_rbw_6_the_author_is_never_booked(self) -> None:
        """RBW-6: Given an unbooked area review whose only owner is the author,
        when the rebooking runs, then it stays unbooked."""
        # given
        area_review = unbooked_review(self.author, 1, (self.author, OwnerRole.PRIMARY))

        # when
        with at(RUN_TIME):
            result = rebooking.retry_unbooked(rebooking.ADMIN)

        # then
        area_review.refresh_from_db()
        self.assertEqual(area_review.state, AreaReviewState.UNBOOKED)
        self.assertEqual(len(result.unbooked), 1)

    def test_rbw_7_mr_enters_review_only_when_every_area_review_is_booked(
        self,
    ) -> None:
        """RBW-7: Given an MR in awaiting_booking with two unbooked area reviews
        and an owner for only one, when the rebooking runs, then the MR stays in
        awaiting_booking; when the second gets an owner and is booked, the MR
        moves to in_review with the mr.in_review audit event."""
        # given
        owner = person(21)
        first = unbooked_review(self.author, 1, (owner, OwnerRole.PRIMARY))
        merge_request = first.merge_request
        second = AreaReview.objects.create(
            merge_request=merge_request, unbooked_since=RUN_TIME
        )
        second_area = CodeArea.objects.create(
            name="second", path_patterns=[], size_limit_lines=100
        )
        second.areas.set([second_area])

        # when / then
        with self.subTest("one area review stays unbooked"):
            with at(RUN_TIME):
                rebooking.retry_unbooked(rebooking.ADMIN)

            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, MergeRequestState.AWAITING_BOOKING)
        with self.subTest("every area review is booked"):
            Ownership.objects.create(
                person=owner, area=second_area, role=OwnerRole.PRIMARY
            )

            with at(RUN_TIME):
                rebooking.retry_unbooked(rebooking.ADMIN)

            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, MergeRequestState.IN_REVIEW)
            event = AuditEvent.objects.get(
                action="mr.in_review", subject_id=str(merge_request.pk)
            )
            self.assertEqual(event.before, {"state": "awaiting_booking"})

    def test_rbw_9_a_failing_area_review_is_counted_and_the_run_goes_on(
        self,
    ) -> None:
        """RBW-9: Given two unbooked area reviews and a booking that raises for
        the first after it wrote its rows, when the rebooking runs, then the error is
        logged, its writes are rolled back, the first
        is listed as still unbooked, and the second is booked."""
        # given
        owner = person(21)
        first = unbooked_review(
            self.author,
            1,
            (owner, OwnerRole.PRIMARY),
            waiting_since=RUN_TIME - timedelta(days=2),
        )
        second = unbooked_review(self.author, 2, (owner, OwnerRole.PRIMARY))
        real = booking.book_area_review

        def flaky(area_review: AreaReview, **kwargs: object) -> bool:
            booked = real(area_review, **kwargs)  # writes rows, then fails
            if area_review.pk == first.pk:
                raise RuntimeError("boom")
            return booked

        # when
        with (
            at(RUN_TIME),
            self.assertLogs("reviews.rebooking", "ERROR"),
            mock.patch.object(booking, "book_area_review", side_effect=flaky),
        ):
            result = rebooking.retry_unbooked(rebooking.ADMIN)

        # then
        self.assertEqual(result.booked, 1)
        self.assertEqual([area.pk for area in result.unbooked], [first.pk])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.state, AreaReviewState.UNBOOKED)
        self.assertEqual(second.state, AreaReviewState.BOOKED)
        self.assertFalse(ReviewSession.objects.filter(area_review=first).exists())
        self.assertEqual(
            [
                call.payload["iid"]
                for call in OutboxCall.objects.filter(kind="set_reviewer")
            ],
            [2],
        )

    def test_rbw_10_one_summary_and_one_audit_event_per_run(self) -> None:
        """RBW-10: Given an unbooked area review that waited three days and one
        with no wait time, when the daily rebooking runs, then no per-review
        no-fit alert is sent, one admin alert lists both with their wait, and one
        booking.retry_run event holds the counts; when nothing is left, no alert
        is sent."""
        # given
        unbooked_review(
            self.author, 1, waiting_since=RUN_TIME - timedelta(days=3, hours=2)
        )
        no_time = unbooked_review(self.author, 2)
        AreaReview.objects.filter(pk=no_time.pk).update(unbooked_since=None)
        MergeRequest.objects.filter(pk=no_time.merge_request_id).update(
            opened_at=RUN_TIME - timedelta(days=5)
        )

        # when
        with at(RUN_TIME):
            rebooking.run_daily_rebooking()

        # then
        self.assertEqual(
            notify_texts(),
            [
                "2 area review(s) still have no reviewer:\n"
                "7:2 MR 2 — area2 — waiting 5 day(s)\n"
                "7:1 MR 1 — area1 — waiting 3 day(s)"
            ],
        )
        event = AuditEvent.objects.get(action="booking.retry_run")
        self.assertEqual(
            event.after, {"trigger": "daily", "booked": 0, "unbooked": 2, "skipped": 0}
        )
        with self.subTest("no alert when nothing is left"):
            OutboxCall.objects.all().delete()
            owner = person(21)
            for area in CodeArea.objects.all():
                Ownership.objects.create(
                    person=owner, area=area, role=OwnerRole.PRIMARY
                )

            with at(RUN_TIME + timedelta(days=1)):
                result = rebooking.run_daily_rebooking()

            self.assertEqual(result.booked, 2)
            self.assertFalse(summaries())


class RebookingConcurrencyTest(TransactionTestCase):
    """The rebooking next to other workers and other paths."""

    def test_rbw_3_rebooking_runs_once_per_day_and_catches_up_after_downtime(
        self,
    ) -> None:
        """RBW-3: Given two workers, when both pass the run time at the same
        moment, then exactly one runs the rebooking; a later pass the same day
        does nothing; after a day the worker was down at the run time, its first
        pass later that day runs it."""
        # given
        author = person(11)
        owner = person(21)
        unbooked_review(author, 1, (owner, OwnerRole.PRIMARY))
        RebookingSettings.load()
        barrier = threading.Barrier(2)

        def run(_: int) -> rebooking.RebookingResult | None:
            try:
                barrier.wait()
                return rebooking.run_daily_rebooking()
            finally:
                connection.close()

        # when
        with at(RUN_TIME), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))

        # then
        self.assertEqual([result is None for result in results].count(True), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="booking.retry_run").count(), 1
        )
        self.assertEqual(ReviewSession.objects.count(), 1)
        with self.subTest("a second pass the same day"):
            with at(RUN_TIME + timedelta(hours=8)):
                self.assertIsNone(rebooking.run_daily_rebooking())

            self.assertEqual(
                AuditEvent.objects.filter(action="booking.retry_run").count(), 1
            )
        with self.subTest("late on a day after downtime at the run time"):
            with at(RUN_TIME + timedelta(days=1, hours=7)):
                self.assertIsNotNone(rebooking.run_daily_rebooking())

            self.assertEqual(
                RebookingSettings.load().last_rebooking_on,
                RUN_TIME.date() + timedelta(days=1),
            )

    def test_rbw_8_rebooking_skips_what_changed_while_it_waits_for_the_lock(
        self,
    ) -> None:
        """RBW-8: Given an unbooked area review selected for rebooking, when
        another path holds the MR lock and merges the MR, sends it to triage or
        back to the AI gate, or books the area review, and commits while the
        rebooking waits, then the rebooking skips it and books nothing."""
        author = person(11)
        owner = person(21)
        for iid, change in (
            (1, {"state": MergeRequestState.MERGED}),
            (2, {"state": MergeRequestState.NEEDS_TRIAGE}),
            (3, {"state": MergeRequestState.AWAITING_AI_REVIEW}),
            (4, "booked"),
        ):
            with self.subTest(change=change):
                # given
                area_review = unbooked_review(author, iid, (owner, OwnerRole.PRIMARY))
                merge_request = area_review.merge_request
                locked = threading.Event()
                release = threading.Event()

                def other_path(
                    merge_request=merge_request,
                    area_review=area_review,
                    change=change,
                    locked=locked,
                    release=release,
                ) -> None:
                    try:
                        with transaction.atomic():
                            MergeRequest.objects.select_for_update().get(
                                pk=merge_request.pk
                            )
                            if change == "booked":
                                AreaReview.objects.filter(pk=area_review.pk).update(
                                    state=AreaReviewState.BOOKED
                                )
                            else:
                                MergeRequest.objects.filter(pk=merge_request.pk).update(
                                    **change
                                )
                            locked.set()
                            release.wait(5)
                    finally:
                        connection.close()

                def retry(merge_request=merge_request) -> rebooking.RebookingResult:
                    try:
                        return rebooking.retry_unbooked(
                            rebooking.ADMIN, [merge_request.pk]
                        )
                    finally:
                        connection.close()

                # when
                with at(RUN_TIME), ThreadPoolExecutor(max_workers=2) as pool:
                    holder = pool.submit(other_path)
                    locked.wait(5)
                    retrying = pool.submit(retry)
                    wait_until_blocked_on_a_lock()
                    release.set()
                    holder.result(timeout=10)
                    result = retrying.result(timeout=10)

                # then
                self.assertEqual(
                    (result.booked, len(result.unbooked), result.skipped),
                    (0, 0, 1),
                )
                self.assertFalse(
                    ReviewSession.objects.filter(area_review=area_review).exists()
                )


class FollowUpPendingTest(TestCase):
    """The rebooking next to a pending follow-up of the area resolver."""

    def test_rbw_23_rebooking_leaves_an_mr_with_a_pending_follow_up_to_it(
        self,
    ) -> None:
        """RBW-23: Given an MR in awaiting_booking with an approved and an
        unbooked area review and a pending resolve_areas job, when the rebooking
        runs, then it skips the MR and the MR stays in awaiting_booking; a
        failed job does not block; the follow-up then resets the stale approval
        and books a follow-up session."""
        # given
        author = person(11)
        owner = person(21)
        unbooked = unbooked_review(author, 1, (owner, OwnerRole.PRIMARY))
        merge_request = unbooked.merge_request
        approved_area = CodeArea.objects.create(
            name="approved", path_patterns=[], size_limit_lines=100
        )
        Ownership.objects.create(
            person=owner, area=approved_area, role=OwnerRole.PRIMARY
        )
        approved = AreaReview.objects.create(
            merge_request=merge_request,
            state=AreaReviewState.APPROVED,
            reviewer=owner,
            unbooked_since=None,
        )
        approved.areas.set([approved_area])
        MergeRequest.objects.filter(pk=merge_request.pk).update(
            ai_gate_entered_at=RUN_TIME
        )
        job = OutboxCall.objects.create(
            kind="resolve_areas", payload={"merge_request_id": merge_request.pk}
        )

        # when / then
        with self.subTest("a pending job"):
            with at(RUN_TIME):
                result = rebooking.retry_unbooked(rebooking.ADMIN)

            self.assertEqual(
                (result.booked, len(result.unbooked), result.skipped), (0, 0, 1)
            )
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, MergeRequestState.AWAITING_BOOKING)
        with self.subTest("the follow-up still runs"):
            gateway = FakeGitLabGateway()
            with (
                at(RUN_TIME),
                mock.patch.object(
                    area_resolver, "get_gitlab_gateway", return_value=gateway
                ),
            ):
                area_resolver.resolve_areas({"merge_request_id": merge_request.pk})

            approved.refresh_from_db()
            unbooked.refresh_from_db()
            self.assertEqual(approved.state, AreaReviewState.BOOKED)
            self.assertTrue(approved.sessions.get().is_continuation)
            self.assertEqual(unbooked.state, AreaReviewState.BOOKED)
        with self.subTest("a failed job does not block"):
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, MergeRequestState.IN_REVIEW)
            MergeRequest.objects.filter(pk=merge_request.pk).update(
                state=MergeRequestState.AWAITING_BOOKING
            )
            AreaReview.objects.filter(pk=unbooked.pk).update(
                state=AreaReviewState.UNBOOKED, reviewer=None, booked_day=None
            )
            unbooked.sessions.update(outcome=ReviewSessionOutcome.SUPERSEDED)
            OutboxCall.objects.filter(pk=job.pk).update(status=OutboxStatus.FAILED)

            with at(RUN_TIME):
                result = rebooking.retry_unbooked(rebooking.ADMIN)

            self.assertEqual(result.booked, 1)


class RebookingAdminTest(TestCase):
    """The admin side of the rebooking."""

    def test_rbw_11_admin_action_retries_selected_mrs_only(self) -> None:
        """RBW-11: Given an MR with an unbooked area review, one with no unbooked
        area review and one that is not selected, when the admin runs "Retry
        booking", then only the selected unbooked one is booked, the counts are
        shown, and the audit trigger is admin."""
        # given
        root = get_user_model().objects.create_superuser("root", password="pw")
        self.client.force_login(root)
        author = person(11)
        owner = person(21)
        selected = unbooked_review(author, 1, (owner, OwnerRole.PRIMARY))
        not_selected = unbooked_review(author, 2, (owner, OwnerRole.PRIMARY))
        booked_already = MergeRequest.objects.create(
            project_id=7,
            iid=3,
            author=author,
            title="MR 3",
            state=MergeRequestState.IN_REVIEW,
        )

        # when
        with at(RUN_TIME):
            response = self.client.post(
                "/admin/reviews/mergerequest/",
                {
                    "action": "retry_booking",
                    "_selected_action": [selected.merge_request_id, booked_already.pk],
                },
                follow=True,
            )

        # then
        self.assertContains(response, "1 booked, 0 still unbooked, 1 skipped.")
        selected.refresh_from_db()
        not_selected.refresh_from_db()
        self.assertEqual(selected.state, AreaReviewState.BOOKED)
        self.assertEqual(not_selected.state, AreaReviewState.UNBOOKED)
        self.assertEqual(
            AuditEvent.objects.get(action="booking.retry_run").after["trigger"],
            "admin",
        )
