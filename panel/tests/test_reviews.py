from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    MergeRequestState,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
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
        panel_login(self.client, me.user)

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

    def test_mrv_3_go_to_mr_link_opens_in_new_tab_only_with_an_address(self):
        """MRV-3: Given a booked review whose MR has a web address and one whose
        MR has none, when I open My reviews, then the first shows a "Go to MR"
        link to a new tab without opener access, and the second shows no link.
        """
        me = create_person(1, "Me", "me")
        author = create_person(3, "Author Ann", "ann")
        area = CodeArea.objects.create(name="payments", size_limit_lines=300)
        today = timezone.localdate()
        with_url = create_merge_request(
            author, 1, "Has link", MergeRequestState.IN_REVIEW
        )
        with_url.web_url = "https://gitlab.example.com/g/p/-/merge_requests/1"
        with_url.save()
        no_url = create_merge_request(author, 2, "No link", MergeRequestState.IN_REVIEW)
        book(me, with_url, area, today, "r1")
        book(me, no_url, area, today, "r2")
        panel_login(self.client, me.user)

        response = self.client.get("/panel/reviews/")

        self.assertContains(
            response,
            '<a class="grow-end" href="https://gitlab.example.com/g/p/-/merge_requests/1"'
            ' target="_blank" rel="noopener noreferrer">Go to MR</a>',
            count=1,
            html=True,
        )

    def test_rvo_9_lists_follow_ups_first_with_outcome_buttons_or_the_outcome(  # pylint: disable=too-many-locals
        self,
    ):
        """RVO-9: Given a follow-up session, a new session and a closed session on
        one day, when I open My reviews, then the follow-up is listed before the
        others, pending sessions show Done, Need more time and Hand back, and the
        closed one shows its outcome instead.
        """
        me = create_person(1, "Me", "me")
        author = create_person(3, "Author Ann", "ann")
        area = CodeArea.objects.create(name="payments", size_limit_lines=300)
        today = timezone.localdate()
        sessions = {}
        for iid, title, outcome, is_continuation in (
            (1, "Plain review", ReviewSessionOutcome.PENDING, False),
            (2, "Closed review", ReviewSessionOutcome.DONE, False),
            (3, "Later follow-up", ReviewSessionOutcome.PENDING, True),
        ):
            merge_request = create_merge_request(
                author, iid, title, MergeRequestState.IN_REVIEW
            )
            review = book(me, merge_request, area, today, "reason")
            sessions[iid] = ReviewSession.objects.create(
                area_review=review,
                reviewer=me,
                day=today,
                slot_minutes=60,
                outcome=outcome,
                is_continuation=is_continuation,
            )
        panel_login(self.client, me.user)

        response = self.client.get("/panel/reviews/")

        self.assertRegex(
            response.content.decode(), r"(?s)Later follow-up.*Plain review"
        )
        self.assertContains(response, "Follow-up</span>", count=1)
        for iid in (1, 3):
            self.assertContains(response, f"/panel/sessions/{sessions[iid].pk}/done/")
            self.assertContains(
                response, f"/panel/sessions/{sessions[iid].pk}/more-time/"
            )
            self.assertContains(
                response, f"/panel/sessions/{sessions[iid].pk}/hand-back/"
            )
        self.assertNotContains(response, f"/panel/sessions/{sessions[2].pk}/")
        self.assertContains(
            response, '<span class="badge booking">Done</span>', count=1, html=True
        )
        with self.subTest(name="overdue_first"):
            # given
            missed_mr = create_merge_request(
                author, 4, "Missed review", MergeRequestState.IN_REVIEW
            )
            missed_review = book(me, missed_mr, area, today - timedelta(days=2), "r")
            missed = ReviewSession.objects.create(
                area_review=missed_review,
                reviewer=me,
                day=today - timedelta(days=2),
                slot_minutes=60,
            )

            # when
            overdue_page = self.client.get("/panel/reviews/")

            # then
            self.assertRegex(
                overdue_page.content.decode(),
                r"(?s)Overdue.*Missed review.*Later follow-up",
            )
            self.assertContains(overdue_page, f"/panel/sessions/{missed.pk}/done/")
            self.assertContains(overdue_page, f"/panel/sessions/{missed.pk}/more-time/")
            self.assertContains(overdue_page, f"/panel/sessions/{missed.pk}/hand-back/")
