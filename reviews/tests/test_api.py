from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Person,
)


class MergeRequestApiTest(TestCase):
    def setUp(self) -> None:
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        reviewer = Person.objects.create(gitlab_user_id=21, name="Reviewer")
        area = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=500
        )
        self.merge_request = MergeRequest.objects.create(
            project_id=7,
            iid=3,
            title="Booked MR",
            author=author,
            state=MergeRequestState.IN_REVIEW,
        )
        self.area_review = AreaReview.objects.create(
            merge_request=self.merge_request,
            state=AreaReviewState.BOOKED,
            reviewer=reviewer,
            booked_day=date(2026, 10, 26),
            slot_minutes=60,
            reason="primary owner; 2026-10-26; 120 of 120 minutes free",
        )
        self.area_review.areas.set([area])
        self.url = f"/api/merge-requests/{self.merge_request.pk}/"

    def test_staff_reads_merge_request_and_anonymous_is_refused(self) -> None:
        """RMO-19: Given a booked MR, when a staff user requests it, then the
        state and area reviews are returned; when an anonymous user requests it,
        then the reply is 401 or 403."""
        with self.subTest(name="anonymous"):
            # when
            response = self.client.get(self.url)

            # then
            self.assertIn(response.status_code, (401, 403))

        with self.subTest(name="non_staff"):
            # given
            user = get_user_model().objects.create_user("plain")
            self.client.force_login(user)

            # when
            response = self.client.get(self.url)

            # then
            self.assertEqual(response.status_code, 403)

        with self.subTest(name="staff"):
            # given
            staff = get_user_model().objects.create_user("staff", is_staff=True)
            self.client.force_login(staff)

            # when
            response = self.client.get(self.url)

            # then
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {
                    "id": self.merge_request.pk,
                    "project_id": 7,
                    "iid": 3,
                    "state": "in_review",
                    "area_reviews": [
                        {
                            "id": self.area_review.pk,
                            "state": "booked",
                            "areas": ["api"],
                            "reviewer": "Reviewer",
                            "booked_day": "2026-10-26",
                            "reason": "primary owner; 2026-10-26; 120 of 120 minutes free",
                        }
                    ],
                },
            )
