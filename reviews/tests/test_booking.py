import threading
from datetime import date
from unittest import mock

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from integrations.models import OutboxCall
from reviews import booking
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    Leave,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
)

MONDAY = date(2026, 10, 26)
TUESDAY = date(2026, 10, 27)
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


class BookingTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(
            gitlab_user_id=11, name="Author", working_days=EVERY_DAY
        )
        patcher = mock.patch.object(booking.timezone, "localdate", return_value=MONDAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def person(self, gitlab_user_id: int, **fields: object) -> Person:
        fields.setdefault("working_days", EVERY_DAY)
        return Person.objects.create(
            gitlab_user_id=gitlab_user_id, name=f"P{gitlab_user_id}", **fields
        )

    def area_review(self, iid: int, *owners: tuple[Person, str]) -> AreaReview:
        """Create an unbooked area review of a new area owned by `owners`."""
        area = CodeArea.objects.create(
            name=f"area{iid}", path_patterns=[], size_limit_lines=100
        )
        for person, role in owners:
            Ownership.objects.create(person=person, area=area, role=role)
        merge_request = MergeRequest.objects.create(
            project_id=7,
            iid=iid,
            author=self.author,
            title="MR",
            state=MergeRequestState.AWAITING_BOOKING,
        )
        area_review = AreaReview.objects.create(merge_request=merge_request)
        area_review.areas.set([area])
        return area_review

    def assert_booked(
        self, area_review: AreaReview, reviewer: Person, day: date
    ) -> None:
        area_review.refresh_from_db()
        self.assertEqual(area_review.state, AreaReviewState.BOOKED)
        self.assertEqual(area_review.reviewer, reviewer)
        self.assertEqual(area_review.booked_day, day)

    def test_books_eligible_owner_on_earliest_day(self) -> None:
        """RMO-12: Given code owners, when an area review is booked, then the
        reviewer is not the author, not on leave, working that day, and has free
        budget, and the day is the earliest such day."""
        with self.subTest(name="author_is_skipped"):
            # given
            backup = self.person(101)
            area_review = self.area_review(
                1, (self.author, OwnerRole.PRIMARY), (backup, OwnerRole.BACKUP)
            )

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, backup, MONDAY)

        with self.subTest(name="leave_is_skipped"):
            # given
            owner = self.person(102)
            Leave.objects.create(person=owner, start_date=MONDAY, end_date=MONDAY)
            area_review = self.area_review(2, (owner, OwnerRole.PRIMARY))

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, owner, TUESDAY)

        with self.subTest(name="non_working_day_is_skipped"):
            # given
            owner = self.person(103, working_days=[1])
            area_review = self.area_review(3, (owner, OwnerRole.PRIMARY))

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, owner, TUESDAY)

        with self.subTest(name="full_day_is_skipped"):
            # given
            owner = self.person(104)
            CapacityDay.objects.create(
                person=owner, day=MONDAY, budget_minutes=120, booked_minutes=120
            )
            area_review = self.area_review(4, (owner, OwnerRole.PRIMARY))

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, owner, TUESDAY)
            self.assertEqual(
                CapacityDay.objects.get(person=owner, day=TUESDAY).booked_minutes, 60
            )

    def test_ranks_candidates_on_the_same_day(self) -> None:
        """RMO-13: Given several candidates that fit the same day, when an area
        review is booked, then primary beats backup, then more free budget, then
        fewer open bookings, then the lowest person id."""
        with self.subTest(name="primary_before_backup"):
            # given
            backup = self.person(101)
            primary = self.person(102)
            area_review = self.area_review(
                1, (backup, OwnerRole.BACKUP), (primary, OwnerRole.PRIMARY)
            )

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, primary, MONDAY)

        with self.subTest(name="more_free_budget"):
            # given
            small = self.person(103, daily_budget_minutes=120)
            large = self.person(104, daily_budget_minutes=180)
            area_review = self.area_review(
                2, (small, OwnerRole.PRIMARY), (large, OwnerRole.PRIMARY)
            )

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, large, MONDAY)

        with self.subTest(name="fewer_open_bookings"):
            # given
            busy = self.person(105)
            idle = self.person(106)
            earlier = self.area_review(50)
            earlier.reviewer = busy
            earlier.state = AreaReviewState.BOOKED
            earlier.save()
            area_review = self.area_review(
                3, (busy, OwnerRole.PRIMARY), (idle, OwnerRole.PRIMARY)
            )

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, idle, MONDAY)

        with self.subTest(name="lowest_person_id"):
            # given
            first = self.person(107)
            second = self.person(108)
            area_review = self.area_review(
                4, (second, OwnerRole.PRIMARY), (first, OwnerRole.PRIMARY)
            )

            # when
            booking.book_area_review(area_review)

            # then
            self.assert_booked(area_review, first, MONDAY)

    def test_booking_stores_reason_and_queues_outbox_calls(self) -> None:
        """RMO-14: Given a booking, when it is made, then the reason names the
        tier, the day and the free budget, and the outbox holds a set-reviewer
        call and a notify call."""
        # given
        owner = self.person(21)
        area_review = self.area_review(1, (owner, OwnerRole.PRIMARY))

        # when
        booking.book_area_review(area_review)

        # then
        area_review.refresh_from_db()
        self.assertEqual(
            area_review.reason, "primary owner; 2026-10-26; 120 of 120 minutes free"
        )
        self.assertEqual(
            OutboxCall.objects.get(kind="set_reviewer").payload,
            {"project_id": 7, "iid": 1, "reviewer_gitlab_user_id": 21},
        )
        self.assertEqual(
            OutboxCall.objects.get(kind="notify").payload["gitlab_user_ids"], [21]
        )

    def test_no_fit_leaves_review_unbooked_and_alerts_admin(self) -> None:
        """RMO-15: Given no candidate fits within the horizon, when an area
        review is booked, then it stays unbooked and an admin alert is queued."""
        # given
        self.person(1, is_admin=True)
        owner = self.person(21)
        Leave.objects.create(
            person=owner, start_date=MONDAY, end_date=date(2026, 12, 31)
        )
        area_review = self.area_review(1, (owner, OwnerRole.PRIMARY))

        # when
        booked = booking.book_area_review(area_review)

        # then
        area_review.refresh_from_db()
        self.assertFalse(booked)
        self.assertEqual(area_review.state, AreaReviewState.UNBOOKED)
        self.assertFalse(OutboxCall.objects.filter(kind="set_reviewer").exists())
        self.assertEqual(
            OutboxCall.objects.get(kind="notify").payload["gitlab_user_ids"], [1]
        )


@override_settings(RMO_BOOKING_HORIZON_DAYS=1, RMO_DEFAULT_DAILY_BUDGET_MINUTES=60)
class BookingConcurrencyTest(TransactionTestCase):
    def test_concurrent_bookings_never_exceed_the_budget(self) -> None:
        """INV-3: Given one owner with a budget of one slot and two area
        reviews, when both are booked at the same time, then exactly one is
        booked and the booked minutes stay within the budget."""
        # given
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        owner = Person.objects.create(
            gitlab_user_id=21, name="Owner", working_days=EVERY_DAY
        )
        area = CodeArea.objects.create(
            name="api", path_patterns=[], size_limit_lines=100
        )
        Ownership.objects.create(person=owner, area=area, role=OwnerRole.PRIMARY)
        area_review_ids = []
        for iid in (1, 2):
            merge_request = MergeRequest.objects.create(
                project_id=7, iid=iid, author=author, title="MR"
            )
            area_review = AreaReview.objects.create(merge_request=merge_request)
            area_review.areas.set([area])
            area_review_ids.append(area_review.pk)
        barrier = threading.Barrier(2)
        results = []

        def book(area_review_id: int) -> None:
            try:
                area_review = AreaReview.objects.get(pk=area_review_id)
                barrier.wait()
                with transaction.atomic():
                    results.append(booking.book_area_review(area_review))
            finally:
                connection.close()

        threads = [threading.Thread(target=book, args=(pk,)) for pk in area_review_ids]

        # when
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # then
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(
            AreaReview.objects.filter(state=AreaReviewState.BOOKED).count(), 1
        )
        self.assertEqual(CapacityDay.objects.get(person=owner).booked_minutes, 60)
