# pylint: disable=duplicate-code,too-many-lines

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest import mock

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from audit.models import AuditEvent
from integrations import outbox
from integrations.fakes import FakeGitLabGateway
from integrations.models import OutboxCall
from reviews import (
    area_resolver,
    booking,
    merge_retry,
    merge_router,
    outcomes,
    rebooking,
    urgency,
)
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    Leave,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    MergeRoutingSettings,
    OwnerRole,
    Ownership,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
    UrgencyRequest,
)
from reviews.tests.test_urgency import wait_until_blocked_on_locks

MONDAY = date(2026, 10, 26)
TUESDAY = date(2026, 10, 27)
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def person(gitlab_user_id: int, **fields: object) -> Person:
    fields.setdefault("working_days", EVERY_DAY)
    fields.setdefault("daily_budget_minutes", 120)
    return Person.objects.create(
        gitlab_user_id=gitlab_user_id, name=f"P{gitlab_user_id}", **fields
    )


def area_review(
    iid: int,
    author: Person,
    *owners: tuple[Person, str],
    urgent: bool = False,
    state: str = AreaReviewState.UNBOOKED,
    mr_state: str = MergeRequestState.AWAITING_BOOKING,
) -> AreaReview:
    """An area review of a new area owned by `owners`, on a new MR."""
    area = CodeArea.objects.create(
        name=f"area{iid}", path_patterns=[], size_limit_lines=100
    )
    for owner, role in owners:
        Ownership.objects.create(person=owner, area=area, role=role)
    merge_request = MergeRequest.objects.create(
        project_id=7,
        iid=iid,
        author=author,
        title="MR",
        state=mr_state,
        urgent_at=timezone.now() if urgent else None,
    )
    review = AreaReview.objects.create(merge_request=merge_request, state=state)
    review.areas.set([area])
    return review


def same_mr_area_review(
    merge_request: MergeRequest, number: int, owner: Person
) -> AreaReview:
    """Another unbooked area review of `merge_request`, of a new area owned by `owner`."""
    area = CodeArea.objects.create(
        name=f"area{number}", path_patterns=[], size_limit_lines=100
    )
    Ownership.objects.create(person=owner, area=area, role=OwnerRole.PRIMARY)
    review = AreaReview.objects.create(merge_request=merge_request)
    review.areas.set([area])
    return review


def fill(owner: Person, day: date, booked: int = 120) -> CapacityDay:
    return CapacityDay.objects.create(
        person=owner, day=day, budget_minutes=120, booked_minutes=booked
    )


def book_manually(review: AreaReview, reviewer: Person, day: date) -> ReviewSession:
    """Book `review` for `reviewer` on `day` with a pending session and its minutes."""
    row, _ = CapacityDay.objects.get_or_create(
        person=reviewer, day=day, defaults={"budget_minutes": 120}
    )
    row.booked_minutes += 60
    row.save()
    review.state = AreaReviewState.BOOKED
    review.reviewer = reviewer
    review.booked_day = day
    review.slot_minutes = 60
    review.save()
    return ReviewSession.objects.create(
        area_review=review, reviewer=reviewer, day=day, slot_minutes=60
    )


def pending_for(review: AreaReview) -> list[ReviewSession]:
    return list(
        ReviewSession.objects.filter(
            area_review=review, outcome=ReviewSessionOutcome.PENDING
        )
    )


def request_for(author: Person, approver: Person, review: AreaReview) -> int:
    merge_request = review.merge_request
    if merge_request.state == MergeRequestState.AWAITING_BOOKING:
        merge_request.state = MergeRequestState.IN_REVIEW
        merge_request.save(update_fields=["state"])
    return UrgencyRequest.objects.create(
        merge_request=merge_request,
        requester=author,
        approver=approver,
        reason="Release blocker",
    ).pk


def texts() -> list[str]:
    return [
        call.payload["text"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


class UrgentBookingTests(TestCase):
    def setUp(self) -> None:
        self.author = person(11)
        self.boss = person(12)
        self.admin = person(13, is_admin=True)
        patcher = mock.patch.object(booking.timezone, "localdate", return_value=MONDAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def approve(self, review: AreaReview) -> None:
        pk = request_for(self.author, self.boss, review)
        urgency.approve(self.boss, pk, "", "user:boss")

    def test_urg_14_approval_books_today_what_is_unbooked_or_later(self):
        """URG-14: Given an MR with an unbooked, a later-day, an approved and a
        today area review, when the urgency is approved, then the unbooked and
        later ones are booked today in the same transaction, and the approved
        and today ones stay as they are."""
        # given
        owner = person(21)
        unbooked = area_review(1, self.author, (owner, OwnerRole.PRIMARY))
        merge_request = unbooked.merge_request
        later = same_mr_area_review(merge_request, 2, owner)
        approved = same_mr_area_review(merge_request, 3, owner)
        today = same_mr_area_review(merge_request, 4, owner)
        book_manually(later, owner, TUESDAY)
        AreaReview.objects.filter(pk=approved.pk).update(state=AreaReviewState.APPROVED)
        today_session = book_manually(today, owner, MONDAY)

        # when
        self.approve(unbooked)

        # then
        merge_request.refresh_from_db()
        self.assertIsNotNone(merge_request.urgent_at)
        for moved in (unbooked, later):
            moved.refresh_from_db()
            self.assertEqual(moved.state, AreaReviewState.BOOKED)
            self.assertEqual(moved.booked_day, MONDAY)
        approved.refresh_from_db()
        self.assertEqual(approved.state, AreaReviewState.APPROVED)
        today.refresh_from_db()
        self.assertEqual(pending_for(today), [today_session])
        self.assertEqual(
            AuditEvent.objects.filter(action="urgency.approved").count(), 1
        )
        self.assertIn("Your urgency request for MR 7:1 was approved.", texts())

        with self.subTest(name="overdue_session_moves_to_today"):
            # given
            overdue_owner = person(22)
            overdue = area_review(5, self.author, (overdue_owner, OwnerRole.PRIMARY))
            old = book_manually(overdue, overdue_owner, MONDAY - timedelta(days=2))

            # when
            self.approve(overdue)

            # then
            old.refresh_from_db()
            self.assertEqual(old.outcome, ReviewSessionOutcome.SUPERSEDED)
            self.assertEqual([s.day for s in pending_for(overdue)], [MONDAY])

    def test_urg_15_urgent_booking_keeps_availability_and_continuity(self):
        """URG-15: Given an urgent MR, when it is booked, then the budget is
        ignored, but an owner on leave, not working today or the author is never
        booked, and the previous reviewer is tried first unless they handed back."""
        with self.subTest(name="budget_is_ignored"):
            # given
            owner = person(21)
            fill(owner, MONDAY)
            review = area_review(
                1, self.author, (owner, OwnerRole.PRIMARY), urgent=True
            )

            # when
            booking.book_area_review(review)

            # then
            review.refresh_from_db()
            self.assertEqual((review.reviewer, review.booked_day), (owner, MONDAY))

        with self.subTest(name="unavailable_owners_are_never_booked"):
            # given
            on_leave = person(22)
            Leave.objects.create(person=on_leave, start_date=MONDAY, end_date=MONDAY)
            day_off = person(23, working_days=[1])
            backup = person(24)
            fill(backup, MONDAY)
            review = area_review(
                2,
                self.author,
                (on_leave, OwnerRole.PRIMARY),
                (day_off, OwnerRole.PRIMARY),
                (self.author, OwnerRole.PRIMARY),
                (backup, OwnerRole.BACKUP),
                urgent=True,
            )

            # when
            booking.book_area_review(review)

            # then
            review.refresh_from_db()
            self.assertEqual((review.reviewer, review.booked_day), (backup, MONDAY))

        with self.subTest(name="previous_reviewer_off_today_co_owner_books_today"):
            # given
            previous, other = person(35), person(36)
            Leave.objects.create(person=previous, start_date=MONDAY, end_date=MONDAY)
            review = self.continuation_case(5, previous, other, "superseded")

            # when
            rebooking.book_with_continuity(review)

            # then
            review.refresh_from_db()
            self.assertEqual((review.reviewer, review.booked_day), (other, MONDAY))

        with self.subTest(name="previous_reviewer_first"):
            # given
            previous, other = person(31), person(32)
            review = self.continuation_case(3, previous, other, "superseded")

            # when
            rebooking.book_with_continuity(review)

            # then
            review.refresh_from_db()
            self.assertEqual((review.reviewer, review.booked_day), (previous, MONDAY))

        with self.subTest(name="handed_back_reviewer_is_skipped"):
            # given
            previous, other = person(33), person(34)
            review = self.continuation_case(4, previous, other, "handed_back")

            # when
            rebooking.book_with_continuity(review)

            # then
            review.refresh_from_db()
            self.assertEqual(review.reviewer, other)

    def continuation_case(
        self, iid: int, previous: Person, other: Person, outcome: str
    ) -> AreaReview:
        """An urgent area review whose latest session of `previous` was closed with `outcome`."""
        review = area_review(
            iid,
            self.author,
            (previous, OwnerRole.BACKUP),
            (other, OwnerRole.PRIMARY),
            urgent=True,
        )
        session = book_manually(review, previous, TUESDAY)
        session.outcome = outcome
        session.save()
        return review

    def test_urg_16_overage_is_recorded_and_the_day_counts_the_slot(self):
        """URG-16: Given an owner at or near the budget, when an urgent booking
        lands on that day, then the session is urgent with the minutes over
        budget, and the CapacityDay counts the slot."""
        for name, booked, overage in (
            ("full", 120, 60),
            ("partly_over", 90, 30),
            ("ends_exactly_at_budget", 60, 0),
            ("day_already_over_budget", 150, 60),
        ):
            with self.subTest(name=name):
                # given
                owner = person(40 + booked)
                fill(owner, MONDAY, booked)
                review = area_review(
                    booked, self.author, (owner, OwnerRole.PRIMARY), urgent=True
                )

                # when
                booking.book_area_review(review)

                # then
                session = ReviewSession.objects.get(area_review=review)
                self.assertTrue(session.is_urgent)
                self.assertEqual(session.overage_minutes, overage)
                self.assertEqual(
                    CapacityDay.objects.get(person=owner, day=MONDAY).booked_minutes,
                    booked + 60,
                )

        with self.subTest(name="within_budget"):
            # given
            owner = person(70)
            review = area_review(
                70, self.author, (owner, OwnerRole.PRIMARY), urgent=True
            )

            # when
            booking.book_area_review(review)

            # then
            session = ReviewSession.objects.get(area_review=review)
            self.assertEqual((session.is_urgent, session.overage_minutes), (True, 0))

    def test_urg_17_later_session_is_superseded_and_its_minutes_released(self):
        """URG-17: Given an area review booked on a later day, when the urgency
        is approved, then that session is superseded, its minutes are released,
        and the area review is booked today."""
        # given
        owner = person(21)
        review = area_review(1, self.author, (owner, OwnerRole.PRIMARY))
        old = book_manually(review, owner, TUESDAY)

        # when
        self.approve(review)

        # then
        old.refresh_from_db()
        self.assertEqual(old.outcome, ReviewSessionOutcome.SUPERSEDED)
        self.assertEqual(
            CapacityDay.objects.get(person=owner, day=TUESDAY).booked_minutes, 0
        )
        self.assertEqual([s.day for s in pending_for(review)], [MONDAY])
        self.assertTrue(
            AuditEvent.objects.filter(action="review_session.superseded").exists()
        )

    def test_urg_18_no_owner_today_books_the_earliest_day_and_alerts(self):
        """URG-18: Given no owner works today, when the urgency is approved,
        then the earliest available day is booked and one alert lists the area
        reviews; when no day fits, the area review is unbooked and one alert
        says so; the daily rebooking sends no urgent alert."""
        with self.subTest(name="later_day_one_alert_for_two_area_reviews"):
            # given
            owner = person(21, working_days=[1])
            first = area_review(1, self.author, (owner, OwnerRole.PRIMARY))
            second = same_mr_area_review(first.merge_request, 2, owner)

            # when
            self.approve(first)

            # then
            for review in (first, second):
                review.refresh_from_db()
                self.assertEqual(review.booked_day, TUESDAY)
            alert = OutboxCall.objects.filter(
                kind="notify", payload__text__startswith="Urgent MR 7:1"
            ).get()
            line = f"Urgent MR 7:1: no owner is available today; booked on {TUESDAY}."
            self.assertEqual(alert.payload["text"], f"{line}\n{line}")
            self.assertEqual(
                alert.payload["gitlab_user_ids"],
                sorted([self.boss.gitlab_user_id, self.admin.gitlab_user_id]),
            )

        with self.subTest(name="nothing_fits"):
            # given
            owner = person(22)
            Leave.objects.create(
                person=owner, start_date=MONDAY, end_date=MONDAY + timedelta(days=30)
            )
            review = area_review(2, self.author, (owner, OwnerRole.PRIMARY))

            # when
            self.approve(review)

            # then
            review.refresh_from_db()
            self.assertEqual(review.state, AreaReviewState.UNBOOKED)
            self.assertIn("Urgent MR 7:2: no owner fits within 10 days.", texts())

        with self.subTest(name="daily_rebooking_sends_no_urgent_alert"):
            # given
            owner = person(23)
            Leave.objects.create(
                person=owner, start_date=MONDAY, end_date=MONDAY + timedelta(days=30)
            )
            area_review(3, self.author, (owner, OwnerRole.PRIMARY), urgent=True)

            # when
            rebooking.retry_unbooked("admin")

            # then
            self.assertFalse(any("Urgent MR 7:3" in text for text in texts()))
            self.assertTrue(any("still have no reviewer" in text for text in texts()))

    def test_urg_27_pending_follow_up_keeps_the_approval_from_booking(self):
        """URG-27: Given an awaiting_booking MR with a pending resolve_areas job,
        when the urgency is approved, then nothing is booked and the state is
        unchanged, and the follow-up then books urgently and resets a stale
        approval; without a pending job, booking the last unbooked area review
        moves the MR to in_review."""
        with self.subTest(name="pending_job_only_marks_urgent"):
            # given
            owner = person(21)
            fill(owner, MONDAY)
            review = area_review(
                1,
                self.author,
                (owner, OwnerRole.PRIMARY),
                state=AreaReviewState.APPROVED,
            )
            review.reviewer = owner
            review.save()
            ReviewSession.objects.create(
                area_review=review,
                reviewer=owner,
                day=MONDAY - timedelta(days=1),
                slot_minutes=60,
                outcome=ReviewSessionOutcome.DONE,
            )
            merge_request = review.merge_request
            outbox.enqueue("resolve_areas", {"merge_request_id": merge_request.pk})
            pk = UrgencyRequest.objects.create(
                merge_request=merge_request,
                requester=self.author,
                approver=self.boss,
                reason="Now",
            ).pk

            # when
            urgency.approve(self.boss, pk, "", "user:boss")

            # then
            merge_request.refresh_from_db()
            review.refresh_from_db()
            self.assertIsNotNone(merge_request.urgent_at)
            self.assertEqual(merge_request.state, MergeRequestState.AWAITING_BOOKING)
            self.assertEqual(review.state, AreaReviewState.APPROVED)
            self.assertEqual(
                ReviewSession.objects.filter(area_review=review).count(), 1
            )

            # when
            gateway = FakeGitLabGateway()
            gateway.reviewer_states[(7, 1, 21)] = "reviewed"
            gateway.latest_verdict_times[(7, 1, 21)] = timezone.now() + timedelta(
                hours=1
            )
            with mock.patch.object(
                area_resolver, "get_gitlab_gateway", return_value=gateway
            ):
                area_resolver.resolve_areas({"merge_request_id": merge_request.pk})

            # then
            review.refresh_from_db()
            follow_up = pending_for(review)[0]
            self.assertEqual(review.state, AreaReviewState.BOOKED)
            self.assertEqual((follow_up.day, follow_up.is_urgent), (MONDAY, True))
            self.assertEqual(follow_up.overage_minutes, 60)

        with self.subTest(name="no_job_moves_the_mr_to_in_review"):
            # given
            owner = person(22)
            unbooked = area_review(2, self.author, (owner, OwnerRole.PRIMARY))
            merge_request = unbooked.merge_request
            pk = UrgencyRequest.objects.create(
                merge_request=merge_request,
                requester=self.author,
                approver=self.boss,
                reason="Now",
            ).pk

            # when
            urgency.approve(self.boss, pk, "", "user:boss")

            # then
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, MergeRequestState.IN_REVIEW)
            self.assertTrue(AuditEvent.objects.filter(action="mr.in_review").exists())

    def test_urg_19_urgency_survives_later_bookings(self):
        """URG-19: Given an MR approved as urgent, when its area review is booked
        later, gets a follow-up, or is handed back, then the urgent rules apply."""
        with self.subTest(name="booked_later"):
            # given
            owner = person(21)
            fill(owner, MONDAY)
            review = area_review(
                1, self.author, (owner, OwnerRole.PRIMARY), urgent=True
            )

            # when
            booking.book_area_review(review)

            # then
            self.assertEqual(ReviewSession.objects.get(area_review=review).day, MONDAY)

        with self.subTest(name="follow_up"):
            # given
            owner = person(22)
            fill(owner, MONDAY)
            review = area_review(
                2, self.author, (owner, OwnerRole.PRIMARY), urgent=True
            )
            book_manually(review, owner, MONDAY)

            # when
            booking.book_follow_up(review)

            # then
            follow_up = ReviewSession.objects.filter(
                area_review=review, outcome=ReviewSessionOutcome.PENDING
            ).get()
            self.assertEqual((follow_up.day, follow_up.is_urgent), (MONDAY, True))

        with self.subTest(name="hand_back"):
            # given
            first, second = person(23), person(24)
            fill(second, MONDAY)
            review = area_review(
                3,
                self.author,
                (first, OwnerRole.PRIMARY),
                (second, OwnerRole.PRIMARY),
                urgent=True,
            )
            session = book_manually(review, first, MONDAY)

            # when
            outcomes.hand_back(first, session.pk, "user:first")

            # then
            new = ReviewSession.objects.filter(
                area_review=review, outcome=ReviewSessionOutcome.PENDING
            ).get()
            self.assertEqual(
                (new.reviewer, new.day, new.is_urgent), (second, MONDAY, True)
            )

        with self.subTest(name="follow_up_never_rebooks_who_handed_back"):
            # given X handed the review back; Y has it on Tuesday and is on leave
            # today, while X works today
            handed, current = person(25), person(26)
            Leave.objects.create(person=current, start_date=MONDAY, end_date=MONDAY)
            review = area_review(
                4,
                self.author,
                (handed, OwnerRole.PRIMARY),
                (current, OwnerRole.PRIMARY),
                urgent=True,
            )
            earlier = book_manually(review, handed, MONDAY)
            earlier.outcome = ReviewSessionOutcome.HANDED_BACK
            earlier.save(update_fields=["outcome"])
            book_manually(review, current, TUESDAY)

            # when
            booking.book_follow_up(review)

            # then Y is booked on their first working day, never X
            new = ReviewSession.objects.filter(
                area_review=review, outcome=ReviewSessionOutcome.PENDING
            ).get()
            self.assertEqual((new.reviewer, new.day), (current, TUESDAY))

    def test_urg_20_overage_today_never_blocks_a_normal_booking_on_another_day(self):
        """URG-20: Given an urgent booking over budget today, when a normal MR is
        booked for the same owner, then it lands tomorrow, tomorrow stays
        untouched by the urgent one, and nothing fails."""
        # given
        owner = person(21)
        fill(owner, MONDAY)
        urgent_review = area_review(
            1, self.author, (owner, OwnerRole.PRIMARY), urgent=True
        )
        normal = area_review(2, self.author, (owner, OwnerRole.PRIMARY))

        # when
        booking.book_area_review(urgent_review)
        tomorrow_after_urgent = CapacityDay.objects.get(
            person=owner, day=TUESDAY
        ).booked_minutes
        booked = booking.book_area_review(normal)

        # then
        self.assertEqual(tomorrow_after_urgent, 0)
        self.assertTrue(booked)
        normal.refresh_from_db()
        self.assertEqual(normal.booked_day, TUESDAY)
        self.assertEqual(
            CapacityDay.objects.get(person=owner, day=TUESDAY).booked_minutes, 60
        )

    def test_urg_21_rebooking_takes_urgent_area_reviews_first(self):
        """URG-21: Given an old non-urgent and a newer urgent unbooked area
        review for one owner with one free slot today, when the rebooking runs,
        then the urgent one is booked first, today."""
        # given
        owner = person(21, daily_budget_minutes=60)
        old = area_review(1, self.author, (owner, OwnerRole.PRIMARY))
        urgent_review = area_review(
            2, self.author, (owner, OwnerRole.PRIMARY), urgent=True
        )
        AreaReview.objects.filter(pk=old.pk).update(
            unbooked_since=timezone.now() - timedelta(days=5)
        )
        AreaReview.objects.filter(pk=urgent_review.pk).update(
            unbooked_since=timezone.now()
        )

        # when
        rebooking.retry_unbooked("admin")

        # then
        old.refresh_from_db()
        urgent_review.refresh_from_db()
        self.assertEqual(urgent_review.booked_day, MONDAY)
        self.assertEqual(old.booked_day, TUESDAY)

    def test_ugf_1_a_merge_owner_with_limit_zero_is_never_chosen(self):
        """UGF-1: Given merge owners whose effective daily limit is 0, when an
        MR is routed, then they are never chosen, urgent or not; a limit of 1
        reached today still gets an urgent MR, over the limit."""
        now = timezone.make_aware(
            timezone.datetime.combine(MONDAY, timezone.datetime.min.time())
        ).replace(hour=8)
        patcher = mock.patch.object(timezone, "now", return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)

        def route(iid: int, urgent: bool, **fields: object):
            Person.objects.filter(is_merge_owner=True).delete()
            MergeAssignment.objects.all().delete()
            owner = person(40, is_merge_owner=True, merge_times=["10:00"], **fields)
            merge_request = MergeRequest.objects.create(
                project_id=7,
                iid=iid,
                author=self.author,
                title="MR",
                state=MergeRequestState.APPROVED,
            )
            with transaction.atomic():
                booking.lock_booking()
                return owner, merge_router.assign_merge(merge_request, urgent=urgent)

        with self.subTest("own limit 0, urgent"):
            self.assertIsNone(route(1, True, daily_merge_limit=0)[1])
        with self.subTest("own limit 0, not urgent"):
            self.assertIsNone(route(2, False, daily_merge_limit=0)[1])
        with self.subTest("default limit 0, own limit empty, urgent"):
            MergeRoutingSettings.objects.update_or_create(
                pk=1, defaults={"default_daily_merge_limit": 0}
            )
            self.assertIsNone(route(3, True, daily_merge_limit=None)[1])
        with self.subTest("own limit 1, reached, urgent"):
            owner, first = route(4, False, daily_merge_limit=1)
            self.assertEqual(first.merge_owner, owner)
            urgent_mr = MergeRequest.objects.create(
                project_id=7,
                iid=5,
                author=self.author,
                title="MR",
                state=MergeRequestState.APPROVED,
            )
            with transaction.atomic():
                booking.lock_booking()
                assignment = merge_router.assign_merge(urgent_mr, urgent=True)
            self.assertEqual(assignment.merge_owner, owner)
            self.assertTrue(assignment.over_limit)

    def test_urg_23_urgent_merge_routing_ignores_only_the_daily_limit(
        self,
    ):  # pylint: disable=too-many-locals,too-many-statements
        """URG-23: Given urgent approved MRs, when they are routed, then the
        earliest window from now is taken over the daily limit with over_limit
        set, the other rules still apply, an open assignment on another day than
        today is replaced on approval, and a non-urgent MR stays under the limit."""
        now = timezone.make_aware(
            timezone.datetime.combine(MONDAY, timezone.datetime.min.time())
        ).replace(hour=8)
        patcher = mock.patch.object(timezone, "now", return_value=now)
        patcher.start()
        self.addCleanup(patcher.stop)

        def approved_mr(iid: int, urgent: bool) -> MergeRequest:
            return MergeRequest.objects.create(
                project_id=7,
                iid=iid,
                author=self.author,
                title="MR",
                state=MergeRequestState.APPROVED,
                urgent_at=now if urgent else None,
            )

        def route(merge_request: MergeRequest, **kwargs: object):
            with transaction.atomic():
                booking.lock_booking()
                return merge_router.assign_merge(merge_request, **kwargs)

        def owner(user_id: int, **fields: object) -> Person:
            return person(
                user_id,
                is_merge_owner=True,
                merge_times=["10:00", "15:00"],
                daily_merge_limit=1,
                **fields,
            )

        def fill_today(merge_owner: Person) -> None:
            MergeAssignment.objects.create(
                merge_request=approved_mr(900 + merge_owner.pk, False),
                merge_owner=merge_owner,
                window=now.replace(hour=15),
            )

        with self.subTest("over the limit today, earliest window"):
            # given
            full = owner(31)
            fill_today(full)
            # when
            assignment = route(approved_mr(1, True))
            # then
            self.assertEqual(assignment.merge_owner, full)
            self.assertEqual(timezone.localtime(assignment.window).hour, 10)
            self.assertTrue(assignment.over_limit)
        MergeAssignment.objects.all().delete()
        Person.objects.filter(is_merge_owner=True).delete()

        with self.subTest("author and reviewer are never chosen"):
            # given
            self.author.is_merge_owner = True
            self.author.merge_times = ["10:00"]
            self.author.save()
            reviewer = owner(32)
            other = owner(33)
            fill_today(other)
            urgent_mr = approved_mr(2, True)
            AreaReview.objects.create(
                merge_request=urgent_mr,
                reviewer=reviewer,
                state=AreaReviewState.APPROVED,
            )
            # when
            assignment = route(urgent_mr)
            # then
            self.assertEqual(assignment.merge_owner, other)
        Person.objects.filter(pk=self.author.pk).update(is_merge_owner=False)
        MergeAssignment.objects.all().delete()
        Person.objects.filter(is_merge_owner=True).delete()

        with self.subTest("an owner on leave today is not chosen"):
            # given
            away = owner(34)
            present = owner(35)
            Leave.objects.create(person=away, start_date=MONDAY, end_date=MONDAY)
            fill_today(present)
            # when
            assignment = route(approved_mr(3, True))
            # then
            self.assertEqual(assignment.merge_owner, present)
        MergeAssignment.objects.all().delete()
        Person.objects.filter(is_merge_owner=True).delete()

        tuesday_ten = timezone.make_aware(
            timezone.datetime.combine(TUESDAY, timezone.datetime.min.time())
        ).replace(hour=10)
        cases = (
            ("later day", tuesday_ten, True),
            ("missed earlier today", now.replace(hour=7), False),
            ("yesterday, missed", now - timedelta(days=1), True),
            ("00:30 today", now.replace(hour=0, minute=30), False),
            (
                "23:30 yesterday",
                now.replace(hour=0, minute=30) - timedelta(hours=1),
                True,
            ),
        )
        for name, window, replaced in cases:
            with self.subTest(f"open assignment {name}"):
                # given
                merge_owner = owner(36)
                merge_request = approved_mr(4, False)
                open_one = MergeAssignment.objects.create(
                    merge_request=merge_request,
                    merge_owner=merge_owner,
                    window=window,
                )
                pk = UrgencyRequest.objects.create(
                    merge_request=merge_request,
                    requester=self.author,
                    approver=self.boss,
                    reason="Release blocker",
                ).pk
                # when
                urgency.approve(self.boss, pk, "", "user:boss")
                # then
                open_one.refresh_from_db()
                current = MergeAssignment.objects.get(
                    merge_request=merge_request, cancelled_at__isnull=True
                )
                if replaced:
                    self.assertIsNotNone(open_one.cancelled_at)
                    self.assertNotEqual(current.pk, open_one.pk)
                    self.assertEqual(timezone.localtime(current.window).date(), MONDAY)
                else:
                    self.assertEqual(current.pk, open_one.pk)
            MergeRequest.objects.filter(pk=merge_request.pk).delete()
            Person.objects.filter(is_merge_owner=True).delete()

        with self.subTest("the retry routes an urgent MR over the limit"):
            # given
            full = owner(38)
            fill_today(full)
            urgent_mr = approved_mr(6, True)
            # when
            merge_retry.retry_unassigned("admin", [urgent_mr.pk])
            # then
            assignment = MergeAssignment.objects.get(merge_request=urgent_mr)
            self.assertEqual(assignment.merge_owner, full)
            self.assertTrue(assignment.over_limit)
        MergeAssignment.objects.all().delete()
        Person.objects.filter(is_merge_owner=True).delete()

        with self.subTest("a normal MR after an urgent over-limit one"):
            # given
            full = owner(39)
            fill_today(full)
            route(approved_mr(7, True))
            # when
            assignment = route(approved_mr(8, False))
            # then
            self.assertNotEqual(timezone.localtime(assignment.window).date(), MONDAY)
            self.assertFalse(assignment.over_limit)
        MergeAssignment.objects.all().delete()
        Person.objects.filter(is_merge_owner=True).delete()

        with self.subTest("a non-urgent MR is not assigned over the limit"):
            # given
            full = owner(37)
            fill_today(full)
            # when
            assignment = route(approved_mr(5, False))
            # then
            self.assertNotEqual(timezone.localtime(assignment.window).date(), MONDAY)
            self.assertFalse(assignment.over_limit)


class ApprovalRaceTests(TransactionTestCase):
    def setUp(self) -> None:
        self.author = person(11)
        self.boss = person(12)

    def run_against_holder(self, hold, first, second) -> None:
        """Run `first` then `second` while `hold` keeps a lock; release once both wait on it."""
        locked = threading.Event()
        release = threading.Event()

        def holder():
            try:
                with transaction.atomic():
                    hold()
                    locked.set()
                    release.wait(10)
            finally:
                connection.close()

        def run(command):
            try:
                command()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=3) as pool:
            holding = pool.submit(holder)
            locked.wait(5)
            results = [pool.submit(run, first)]
            try:
                wait_until_blocked_on_locks(1)
                results.append(pool.submit(run, second))
                wait_until_blocked_on_locks(2)
            finally:
                release.set()
            holding.result(timeout=10)
            for result in results:
                result.result(timeout=10)

    def test_urg_24_approval_and_rebooking_leave_one_session_today(self):
        """URG-24: Given an unbooked area review of an MR whose owner is full
        today, when the approval and the rebooking handle it at the same time
        (both waiting on the MR lock), then it has exactly one pending session,
        booked today."""
        # given
        today = timezone.localdate()
        owner = person(21)
        fill(owner, today)
        review = area_review(1, self.author, (owner, OwnerRole.PRIMARY))
        request_pk = request_for(self.author, self.boss, review)
        merge_request_pk = review.merge_request_id

        def hold():
            MergeRequest.objects.select_for_update().get(pk=merge_request_pk)

        # when
        self.run_against_holder(
            hold,
            lambda: urgency.approve(self.boss, request_pk, "", "user:boss"),
            lambda: rebooking.retry_unbooked("admin", [merge_request_pk]),
        )

        # then
        sessions = pending_for(review)
        self.assertEqual([s.day for s in sessions], [today])

    def test_urg_25_approval_and_hand_back_leave_one_session_today(self):
        """URG-25: Given an area review booked today with a reviewer who hands
        it back while the urgency is approved (both waiting on the booking
        lock, in either order), then it has one pending session today, not
        with the reviewer who handed it back. The booking lock is what keeps
        the capacity rows in one order (LOCK-1, LOCK-3); the MR lock is proven
        by the decision race test in test_urgency.py."""
        for name, hand_back_first in (
            ("approval_first", False),
            ("hand_back_first", True),
        ):
            with self.subTest(name=name):
                # given
                today = timezone.localdate()
                base = 31 if hand_back_first else 21
                first, second = person(base), person(base + 1)
                fill(second, today)
                review = area_review(
                    base,
                    self.author,
                    (first, OwnerRole.PRIMARY),
                    (second, OwnerRole.PRIMARY),
                )
                session = book_manually(review, first, today)
                request_pk = request_for(self.author, self.boss, review)

                def approve(request_pk=request_pk):
                    urgency.approve(self.boss, request_pk, "", "user:boss")

                def hand_back(first=first, session=session):
                    outcomes.hand_back(first, session.pk, "user:first")

                # when
                self.run_against_holder(
                    booking.lock_booking,
                    hand_back if hand_back_first else approve,
                    approve if hand_back_first else hand_back,
                )

                # then
                sessions = pending_for(review)
                self.assertEqual(
                    [(s.reviewer, s.day) for s in sessions], [(second, today)]
                )
