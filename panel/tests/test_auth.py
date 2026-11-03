import os
import subprocess
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from orchestrator.settings import parse_panel_auth_modes
from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_person

User = get_user_model()
STAFF_LOGIN = {"username": "staff", "password": "pw"}


def create_staff_person():
    """Create a staff user named `staff` linked to a Person."""
    person = create_person(1, "Staff", "staff")
    person.user.is_staff = True
    person.user.is_superuser = True
    person.user.save()
    return person


class PanelAuthTests(TestCase):
    """One test per panel authentication criterion."""

    def test_pau_1_panel_sign_in_uses_its_own_cookie_and_does_not_open_admin(self):
        """PAU-1: Given a staff user, when they sign in on the panel,
        then only the panel cookie is set and the admin still asks to sign in.
        """
        create_staff_person()

        response = self.client.post("/panel/login/", STAFF_LOGIN)

        self.assertRedirects(response, "/panel/", fetch_redirect_response=False)
        self.assertEqual(response.cookies["panel_sessionid"]["path"], "/panel/")
        self.assertNotIn("sessionid", response.cookies)
        admin_response = self.client.get("/admin/")
        self.assertRedirects(
            admin_response, "/admin/login/?next=/admin/", fetch_redirect_response=False
        )
        panel_key = self.client.cookies["panel_sessionid"].value
        self.client.cookies.clear()
        with self.subTest(copy="panel key in the admin cookie"):
            self.client.cookies["sessionid"] = panel_key

            response = self.client.get("/admin/")

            self.assertRedirects(
                response, "/admin/login/?next=/admin/", fetch_redirect_response=False
            )
        self.client.cookies.clear()
        self.client.force_login(User.objects.get(username="staff"))
        admin_key = self.client.cookies["sessionid"].value
        self.client.cookies.clear()
        with self.subTest(copy="admin key in the panel cookie"):
            self.client.cookies["panel_sessionid"] = admin_key

            response = self.client.get("/panel/mrs/")

            self.assertRedirects(
                response,
                "/panel/login/?next=/panel/mrs/",
                fetch_redirect_response=False,
            )

    def test_pau_2_admin_session_does_not_open_the_panel(self):
        """PAU-2: Given a user signed in to the admin only,
        when a panel page opens, then it redirects to the panel login page.
        """
        person = create_staff_person()
        self.client.force_login(person.user)

        response = self.client.get("/panel/mrs/")

        self.assertRedirects(
            response, "/panel/login/?next=/panel/mrs/", fetch_redirect_response=False
        )

    def test_pau_3_sign_out_of_one_side_keeps_the_other_signed_in(self):
        """PAU-3: Given a user signed in to both the panel and the admin,
        when they sign out of one, then the other stays signed in.
        """
        person = create_staff_person()
        self.client.force_login(person.user)
        panel_login(self.client, person.user)

        self.client.post("/panel/logout/")

        with self.subTest(side="admin after panel sign-out"):
            self.assertEqual(self.client.get("/admin/").status_code, 200)
        with self.subTest(side="panel after panel sign-out"):
            self.assertEqual(self.client.get("/panel/mrs/").status_code, 302)
        panel_login(self.client, person.user)
        self.client.post("/admin/logout/")
        with self.subTest(side="panel after admin sign-out"):
            self.assertEqual(self.client.get("/panel/mrs/").status_code, 200)
        with self.subTest(side="admin after admin sign-out"):
            self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_pau_4_local_login_follows_the_enabled_modes(self):
        """PAU-4: Given local sign-in on or off, when the login page opens
        or a login is posted, then the form and the sign-in follow the setting.
        """
        create_staff_person()
        with (
            self.subTest(local="on: form shown"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            self.assertContains(self.client.get("/panel/login/"), 'name="username"')
        with (
            self.subTest(local="on: valid login accepted"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            response = self.client.post("/panel/login/", STAFF_LOGIN)

            self.assertRedirects(response, "/panel/", fetch_redirect_response=False)
        with (
            self.subTest(next="/admin/ is not followed"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            response = self.client.post(
                "/panel/login/", {**STAFF_LOGIN, "next": "/admin/"}
            )

            self.assertRedirects(response, "/panel/", fetch_redirect_response=False)
        with (
            self.subTest(next="dot segments cannot climb out of the panel"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            response = self.client.post(
                "/panel/login/", {**STAFF_LOGIN, "next": "/panel/../admin/"}
            )

            self.assertRedirects(response, "/panel/", fetch_redirect_response=False)
        with (
            self.subTest(next="encoded dot segments cannot climb out of the panel"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            response = self.client.post(
                "/panel/login/", {**STAFF_LOGIN, "next": "/panel/%2e%2e/admin/"}
            )

            self.assertRedirects(response, "/panel/", fetch_redirect_response=False)
        with (
            self.subTest(next="encoded slashes cannot climb out of the panel"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            response = self.client.post(
                "/panel/login/", {**STAFF_LOGIN, "next": "/panel/..%2f..%2fadmin/"}
            )

            self.assertRedirects(response, "/panel/", fetch_redirect_response=False)
        with (
            self.subTest(next="panel page is followed"),
            override_settings(PANEL_AUTH_MODES=("local",)),
        ):
            response = self.client.post(
                "/panel/login/", {**STAFF_LOGIN, "next": "/panel/reviews/"}
            )

            self.assertRedirects(
                response, "/panel/reviews/", fetch_redirect_response=False
            )
        self.client.cookies.clear()
        with (
            self.subTest(local="off: no form"),
            override_settings(PANEL_AUTH_MODES=("sso",)),
        ):
            response = self.client.get("/panel/login/")

            self.assertNotContains(response, 'name="username"')
        with (
            self.subTest(local="off: post is 404"),
            override_settings(PANEL_AUTH_MODES=("sso",)),
        ):
            response = self.client.post("/panel/login/", STAFF_LOGIN)

            self.assertEqual(response.status_code, 404)
            self.assertNotIn("panel_sessionid", self.client.cookies)

    def test_pau_5_sso_stub_follows_the_enabled_modes_and_signs_no_one_in(self):
        """PAU-5: Given sso on or off, when the login page or an SSO URL opens,
        then the button and the stub follow the setting and nobody signs in.
        """
        with override_settings(PANEL_AUTH_MODES=("local", "sso")):
            with self.subTest(sso="on: button shown"):
                self.assertContains(
                    self.client.get("/panel/login/"), "Sign in with GitLab"
                )
            for url in ["/panel/sso/start/", "/panel/sso/callback/"]:
                with self.subTest(sso="on", url=url):
                    response = self.client.get(url)

                    self.assertContains(
                        response, "GitLab sign-in is not set up yet", status_code=501
                    )
                    self.assertContains(
                        response, 'href="/panel/login/"', status_code=501
                    )
                    self.assertNotIn("panel_sessionid", self.client.cookies)
        with override_settings(PANEL_AUTH_MODES=("local",)):
            for url in ["/panel/sso/start/", "/panel/sso/callback/"]:
                with self.subTest(sso="off", url=url):
                    self.assertEqual(self.client.get(url).status_code, 404)

    def test_pau_6_settings_reject_empty_and_unknown_modes(self):
        """PAU-6: Given an empty or unknown mode list, when settings parse it,
        then they raise a configuration error.
        """
        for raw in ["", "ldap", "local,ldap"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ImproperlyConfigured):
                    parse_panel_auth_modes(raw)
        with self.subTest(raw="local,sso"):
            self.assertEqual(parse_panel_auth_modes("local,sso"), ("local", "sso"))
        with self.subTest(startup="bogus mode stops the settings import"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; os.environ.setdefault("
                    "'DJANGO_SETTINGS_MODULE','orchestrator.settings'); "
                    "import orchestrator.settings",
                ],
                env={**os.environ, "PANEL_AUTH_MODES": "bogus", "DEBUG": "1"},
                cwd=settings.BASE_DIR,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ImproperlyConfigured", result.stderr)
