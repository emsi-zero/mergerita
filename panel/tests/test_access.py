from django.contrib.auth import get_user_model
from django.test import TestCase

PANEL_URLS = ["/panel/", "/panel/mrs/", "/panel/mrs/1/"]


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
                    f"/accounts/login/?next={url}",
                    fetch_redirect_response=False,
                )
        with self.subTest(url="/accounts/password_reset/"):
            self.assertEqual(
                self.client.get("/accounts/password_reset/").status_code, 404
            )

    def test_dvp_2_user_without_person_gets_403_not_linked_page(self):
        """DVP-2: Given a user with no Person,
        when a panel page opens, then it replies 403 with the not-linked page.
        """
        user = get_user_model().objects.create_user("ghost", password="pw")
        self.client.force_login(user)

        for url in PANEL_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 403)
                self.assertContains(
                    response, "not linked to a developer yet", status_code=403
                )
