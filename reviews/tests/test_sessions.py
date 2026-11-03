from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from reviews import booking
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
)

EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


class ReviewSessionTests(TestCase):
    def test_rvo_1_every_booking_is_a_pending_session_cached_on_the_area_review(
        self,
    ) -> None:
        """RVO-1: Given an unbooked area review, when it is booked and then
        booked again as a continuation, then each booking is a pending session
        with reviewer, day, minutes and reason, and the area review shows the
        reviewer and day of its latest session.
        """
        # given
        author = Person.objects.create(gitlab_user_id=1, name="Author")
        owner = Person.objects.create(
            gitlab_user_id=2, name="Owner", working_days=EVERY_DAY
        )
        area = CodeArea.objects.create(name="api", path_patterns=[], size_limit_lines=9)
        Ownership.objects.create(person=owner, area=area, role=OwnerRole.PRIMARY)
        merge_request = MergeRequest.objects.create(
            project_id=7,
            iid=1,
            author=author,
            title="MR",
            state=MergeRequestState.AWAITING_BOOKING,
        )
        area_review = AreaReview.objects.create(merge_request=merge_request)
        area_review.areas.set([area])
        today = timezone.localdate()

        # when
        booking.book_area_review(area_review)
        first = ReviewSession.objects.get()
        first.outcome = ReviewSessionOutcome.MORE_TIME
        first.save()
        booking.book_area_review(
            area_review, only_reviewer=owner, after_day=today, continuation=True
        )

        # then
        area_review.refresh_from_db()
        second = ReviewSession.objects.get(outcome=ReviewSessionOutcome.PENDING)
        with self.subTest(name="first_session"):
            self.assertEqual(first.area_review, area_review)
            self.assertEqual(first.reviewer, owner)
            self.assertEqual(first.day, today)
            self.assertEqual(first.slot_minutes, 60)
            self.assertIn("primary owner", first.reason)
            self.assertFalse(first.is_continuation)
        with self.subTest(name="continuation_session"):
            self.assertEqual(second.day, today + timedelta(days=1))
            self.assertTrue(second.is_continuation)
        with self.subTest(name="area_review_cache"):
            self.assertEqual(area_review.state, AreaReviewState.BOOKED)
            self.assertEqual(area_review.reviewer, second.reviewer)
            self.assertEqual(area_review.booked_day, second.day)
