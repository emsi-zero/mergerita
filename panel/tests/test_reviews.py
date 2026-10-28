from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    MergeRequestState,
    Person,
)


def book(
    reviewer: Person, merge_request, area: CodeArea, day, reason: str
) -> AreaReview:
    review = AreaReview.objects.create(
        merge_request=merge_request,
        state=AreaReviewState.BOOKED,
        reviewer=reviewer,
        booked_day=day,
        slot_minutes=60,
        reason=reason,
    )
    review.areas.add(area)
    return review


class MyReviewsTests(TestCase):
    def test_dvp_6_lists_own_bookings_in_horizon_grouped_by_day(self):
        """DVP-6: Given bookings of two reviewers inside and outside the horizon,
        when I open My reviews, then I see only mine from today to the horizon,
        grouped by day with booked minutes and budget.
        """
        me = create_person(1, "Me", "me")
        me.daily_budget_minutes = 180
        me.save()
        other = create_person(2, "Other", "other")
        author = create_person(3, "Author Ann", "ann")
        area = CodeArea.objects.create(name="payments", size_limit_lines=300)
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        merge_request = create_merge_request(
            author, 1, "Card retries", MergeRequestState.IN_REVIEW
        )
        book(me, merge_request, area, today, "primary owner today")
        book(me, merge_request, area, today, "second slot today")
        book(me, merge_request, area, tomorrow, "primary owner tomorrow")
        book(me, merge_request, area, today + timedelta(days=2), "no row reason")
        book(me, merge_request, area, today - timedelta(days=1), "yesterday reason")
        book(me, merge_request, area, today + timedelta(days=10), "beyond reason")
        book(other, merge_request, area, today, "someone else reason")
        CapacityDay.objects.create(
            person=me, day=today, budget_minutes=180, booked_minutes=120
        )
        CapacityDay.objects.create(
            person=me, day=tomorrow, budget_minutes=60, booked_minutes=60
        )
        self.client.force_login(me.user)

        response = self.client.get("/panel/reviews/")

        self.assertContains(response, "Today · ")
        self.assertContains(response, "120 of 180 min booked")
        self.assertContains(response, "60 of 180 min booked")
        self.assertContains(response, "0 of 180 min booked")
        self.assertContains(response, "Card retries", count=4)
        self.assertContains(response, "primary owner today")
        self.assertContains(response, "second slot today")
        self.assertContains(response, "primary owner tomorrow")
        self.assertNotContains(response, "yesterday reason")
        self.assertNotContains(response, "beyond reason")
        self.assertNotContains(response, "someone else reason")
