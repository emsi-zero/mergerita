from django.contrib.auth import get_user_model
from django.test import TestCase

from reviews.models import MergeRoutingSettings, Person


class MergeSettingsAdminTest(TestCase):
    """Merge routing validation in the admin."""

    def setUp(self) -> None:
        admin = get_user_model().objects.create_superuser("root", password="pw")
        self.client.force_login(admin)

    def test_mrg_10_admin_rejects_bad_merge_times_limits_and_window_lengths(
        self,
    ) -> None:
        """MRG-10: Given an admin form, when it holds a merge time that is not
        HH:MM, a negative daily limit or a window length of zero, then it is
        rejected with an error and nothing is saved."""
        # given
        person = Person.objects.create(gitlab_user_id=11, name="P")
        settings_row = MergeRoutingSettings.load()
        person_url = f"/admin/reviews/person/{person.pk}/change/"
        settings_url = f"/admin/reviews/mergeroutingsettings/{settings_row.pk}/change/"
        person_data = {
            "gitlab_user_id": "11",
            "name": "P",
            "working_days": "[0, 1]",
            "is_merge_owner": "on",
            "merge_times": "10:00",
            "daily_merge_limit": "3",
        }

        # when / then
        for field, value in (
            ("merge_times", "10:00, 24:00"),
            ("merge_times", "9:30"),
            ("merge_times", "10:60"),
            ("daily_merge_limit", "-1"),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.post(person_url, {**person_data, field: value})

                self.assertContains(response, "errornote")
                person.refresh_from_db()
                self.assertEqual(person.merge_times, [])
        with self.subTest(field="merge_window_minutes", value="0"):
            response = self.client.post(
                settings_url,
                {"default_daily_merge_limit": "5", "merge_window_minutes": "0"},
            )

            self.assertContains(response, "errornote")
            settings_row.refresh_from_db()
            self.assertEqual(settings_row.merge_window_minutes, 30)
        with self.subTest(field="valid"):
            response = self.client.post(person_url, person_data)

            self.assertEqual(response.status_code, 302)
            person.refresh_from_db()
            self.assertEqual(person.merge_times, ["10:00"])
