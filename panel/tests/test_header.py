from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeAssignment,
    MergeRequestState,
    Ownership,
    ReviewSession,
    ReviewSessionOutcome,
)


class PanelHeaderTests(TestCase):
    """The person, their areas and the tab counts in the panel header."""

    def test_pns_2_header_shows_the_person_areas_and_tab_counts(self):
        """PNS-2: Given my areas, open and merged MRs, pending, done, overdue and
        far-off review sessions, and merge assignments, when I open a panel page,
        then the header shows my name, initials and areas, and the tabs count my
        open MRs, pending sessions up to the horizon and open merges; a person
        with nothing sees no counts and a non-merge owner no merge count."""
        # given
        me = create_person(1, "Reza Karimi", "reza")
        me.is_merge_owner = True
        me.save()
        other = create_person(2, "Other", "other")
        for name in ("web", "api"):
            Ownership.objects.create(
                person=me,
                area=CodeArea.objects.create(name=name, size_limit_lines=400),
                role="primary",
            )
        Ownership.objects.create(
            person=other,
            area=CodeArea.objects.create(name="infra", size_limit_lines=400),
            role="primary",
        )
        create_merge_request(me, 1, "Open", MergeRequestState.IN_REVIEW)
        create_merge_request(me, 2, "Waiting", MergeRequestState.WAITING_ON_AUTHOR)
        create_merge_request(me, 3, "Merged", MergeRequestState.MERGED)
        theirs = create_merge_request(other, 4, "Theirs", MergeRequestState.APPROVED)
        horizon = settings.RMO_BOOKING_HORIZON_DAYS
        for reviewer, offset, outcome in (
            (me, 0, ReviewSessionOutcome.PENDING),
            (me, -2, ReviewSessionOutcome.PENDING),
            (me, horizon - 1, ReviewSessionOutcome.PENDING),
            (me, horizon, ReviewSessionOutcome.PENDING),
            (me, 0, ReviewSessionOutcome.DONE),
            (other, 0, ReviewSessionOutcome.PENDING),
        ):
            day = timezone.localdate() + timedelta(days=offset)
            area_review = AreaReview.objects.create(
                merge_request=theirs,
                state=AreaReviewState.BOOKED,
                reviewer=reviewer,
                booked_day=day,
                slot_minutes=60,
            )
            ReviewSession.objects.create(
                area_review=area_review,
                reviewer=reviewer,
                day=day,
                slot_minutes=60,
                outcome=outcome,
            )
        for merged_at, cancelled_at in (
            (None, None),
            (timezone.now(), None),
            (None, timezone.now()),
        ):
            MergeAssignment.objects.create(
                merge_request=theirs,
                merge_owner=me,
                window=timezone.now() + timedelta(hours=2),
                reason="r",
                merged_at=merged_at,
                cancelled_at=cancelled_at,
            )
        panel_login(self.client, me.user)

        # when
        response = self.client.get("/panel/availability/")

        # then
        self.assertEqual(
            response.context["tab_counts"], {"mrs": 2, "reviews": 3, "merges": 1}
        )
        self.assertContains(response, "Reza Karimi")
        self.assertContains(response, '<div class="avatar" aria-hidden="true">RK</div>')
        self.assertContains(response, "api · web")
        self.assertNotContains(response, "infra")
        self.assertContains(response, 'My MRs<span class="count">2</span>')
        self.assertContains(response, 'My reviews<span class="count">3</span>')
        self.assertContains(response, 'My merges<span class="count">1</span>')
        with self.subTest("nothing to count, not a merge owner"):
            panel_login(self.client, create_person(5, "New Person", "new").user)

            response = self.client.get("/panel/availability/")

            self.assertEqual(response.context["tab_counts"], {"mrs": 0, "reviews": 0})
            self.assertNotContains(response, 'class="count"')
