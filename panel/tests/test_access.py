from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_person
from reviews.models import Leave

PANEL_URLS = [
    "/panel/",
    "/panel/mrs/",
    "/panel/mrs/1/",
    "/panel/reviews/",
    "/panel/availability/",
]


POST_URLS = [
    "/panel/availability/days/",
    "/panel/availability/budget/",
    "/panel/availability/leave/",
    "/panel/availability/leave/1/delete/",
]
POST_DATA = {
    "days": ["1"],
    "minutes": "240",
    "start_date": "2026-11-15",
    "end_date": "2026-11-16",
}


class PanelAccessTests(TestCase):
    def test_dvp_1_anonymous_visitor_is_redirected_to_login(self):
        """DVP-1: Given no signed-in user,
        when a panel page opens, then it redirects to login.
        """
        for url in PANEL_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertRedirects(
                    response,
                    f"/panel/login/?next={url}",
                    fetch_redirect_response=False,
                )
        owner = create_person(1, "Owner", "owner")
        leave = Leave.objects.create(
            person=owner, start_date=date(2026, 12, 1), end_date=date(2026, 12, 2)
        )
        for url in POST_URLS:
            with self.subTest(post_url=url):
                response = self.client.post(url, POST_DATA)

                self.assertRedirects(
                    response,
                    f"/panel/login/?next={url}",
                    fetch_redirect_response=False,
                )
        with self.subTest(url="no data changed"):
            owner.refresh_from_db()
            self.assertEqual(owner.working_days, [5, 6, 0, 1, 2])
            self.assertIsNone(owner.daily_budget_minutes)
            self.assertEqual(list(Leave.objects.all()), [leave])
        with self.subTest(url="/accounts/password_reset/"):
            self.assertEqual(
                self.client.get("/accounts/password_reset/").status_code, 404
            )

    def test_dvp_2_user_without_person_gets_403_not_linked_page(self):
        """DVP-2: Given a user with no Person,
        when a panel page opens, then it replies 403 with the not-linked page.
        """
        user = get_user_model().objects.create_user("ghost", password="pw")
        panel_login(self.client, user)

        for url in PANEL_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 403)
                self.assertContains(
                    response, "not linked to a developer yet", status_code=403
                )
        owner = create_person(1, "Owner", "owner")
        leave = Leave.objects.create(
            person=owner, start_date=date(2026, 12, 1), end_date=date(2026, 12, 2)
        )
        for url in POST_URLS:
            with self.subTest(post_url=url):
                response = self.client.post(url, POST_DATA)

                self.assertEqual(response.status_code, 403)
        with self.subTest(url="no data changed"):
            owner.refresh_from_db()
            self.assertEqual(owner.working_days, [5, 6, 0, 1, 2])
            self.assertIsNone(owner.daily_budget_minutes)
            self.assertEqual(list(Leave.objects.all()), [leave])
