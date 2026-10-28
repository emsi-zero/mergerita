from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.models import AuditEvent
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Person,
)


def create_person(gitlab_user_id: int, name: str, username: str) -> Person:
    user = get_user_model().objects.create_user(username, password="pw")
    return Person.objects.create(gitlab_user_id=gitlab_user_id, name=name, user=user)


def create_merge_request(
    author: Person, iid: int, title: str, state: str, changed_lines: int = 10
) -> MergeRequest:
    return MergeRequest.objects.create(
        project_id=7,
        iid=iid,
        author=author,
        title=title,
        state=state,
        changed_lines=changed_lines,
    )


class MergeRequestPanelTests(TestCase):
    def setUp(self) -> None:
        self.me = create_person(1, "Me", "me")
        self.other = create_person(2, "Other", "other")
        self.client.force_login(self.me.user)

    def test_dvp_3_list_shows_only_own_merge_requests(self):
        """DVP-3: Given MRs of two authors,
        when I open My MRs, then I see only mine with title, state label and summary.
        """
        create_merge_request(
            self.me, 1, "My waiting MR", MergeRequestState.WAITING_ON_AUTHOR
        )
        create_merge_request(
            self.me, 2, "My review MR", MergeRequestState.AWAITING_AI_REVIEW
        )
        create_merge_request(
            self.other, 3, "Their secret MR", MergeRequestState.IN_REVIEW
        )

        response = self.client.get("/panel/mrs/")

        self.assertContains(response, "My waiting MR")
        self.assertContains(response, "Your move")
        self.assertContains(response, "AI Reviewer requested changes")
        self.assertContains(response, "My review MR")
        self.assertContains(response, "Waiting for the AI Reviewer")
        self.assertNotContains(response, "Their secret MR")

    def test_dvp_4_detail_shows_reviews_next_step_history_and_hides_others(self):
        """DVP-4: Given my booked MR,
        when I open it, then I see reviews, next step and history; another's MR is 404.
        """
        reviewer = create_person(3, "Sara Reviewer", "sara")
        area = CodeArea.objects.create(
            name="payments", path_patterns=["p/*"], size_limit_lines=500
        )
        mine = create_merge_request(self.me, 1, "Mine", MergeRequestState.IN_REVIEW)
        area_review = AreaReview.objects.create(
            merge_request=mine,
            state=AreaReviewState.BOOKED,
            reviewer=reviewer,
            booked_day=date(2026, 10, 29),
            reason="primary owner; 120 of 120 minutes free",
        )
        area_review.areas.set([area])
        AuditEvent.objects.create(
            actor="gitlab",
            action="mr.opened",
            subject_type="reviews.MergeRequest",
            subject_id=str(mine.pk),
        )
        AuditEvent.objects.create(
            actor="system",
            action="area_review.booked",
            subject_type="reviews.AreaReview",
            subject_id=str(area_review.pk),
        )
        AuditEvent.objects.create(
            actor="system",
            action="mr.opened",
            subject_type="reviews.MergeRequest",
            subject_id=str(mine.pk + 1000),
        )
        theirs = create_merge_request(
            self.other, 2, "Theirs", MergeRequestState.IN_REVIEW
        )

        their_review = AreaReview.objects.create(
            merge_request=theirs, reason="DECOY-REASON"
        )
        AuditEvent.objects.create(
            actor="system",
            action="area_review.created",
            subject_type="reviews.AreaReview",
            subject_id=str(their_review.pk),
            after={"note": "DECOY-HISTORY"},
        )

        response = self.client.get(f"/panel/mrs/{mine.pk}/")
        other_response = self.client.get(f"/panel/mrs/{theirs.pk}/")

        self.assertContains(response, "payments")
        self.assertContains(response, "Sara Reviewer")
        self.assertContains(response, "Thu 29 Oct")
        self.assertContains(response, "primary owner; 120 of 120 minutes free")
        self.assertContains(response, "Nothing to do")
        self.assertContains(response, "Opened")
        self.assertContains(response, "Reviewer booked")
        self.assertEqual(len(response.context["history"]), 2)
        self.assertNotContains(response, "DECOY")
        self.assertNotContains(response, "Area review created")
        self.assertEqual(other_response.status_code, 404)

    def test_dvp_5_detail_warns_when_size_exceeds_an_area_limit(self):
        """DVP-5: Given an MR over an area's limit,
        when I open it, then a size warning shows; at the limit it does not.
        """
        area = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=400
        )
        for changed_lines, expect_warning in ((401, True), (400, False)):
            with self.subTest(changed_lines=changed_lines):
                merge_request = create_merge_request(
                    self.me,
                    changed_lines,
                    "Sized",
                    MergeRequestState.AWAITING_BOOKING,
                    changed_lines,
                )
                AreaReview.objects.create(merge_request=merge_request).areas.set([area])

                response = self.client.get(f"/panel/mrs/{merge_request.pk}/")

                self.assertEqual(
                    response.context["size_warning"] is not None, expect_warning
                )
                if expect_warning:
                    self.assertContains(response, "Over the 400-line limit of the api")
