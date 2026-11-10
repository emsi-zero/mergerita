from django.contrib.auth import get_user_model
from django.test import TestCase

from reviews.models import Person, Team


def person_form(person: Person, **extra: object) -> dict:
    return {
        "gitlab_user_id": str(person.gitlab_user_id),
        "name": person.name,
        "working_days": "[0, 1]",
        "merge_times": "",
        **extra,
    }


class TeamAdminTests(TestCase):
    """Teams and reporting lines in the admin."""

    def setUp(self) -> None:
        root = get_user_model().objects.create_superuser("root", password="pw")
        self.client.force_login(root)

    def test_urg_1_admin_creates_a_team_and_sets_team_and_reports_to(self) -> None:
        """URG-1: Given two people, when an admin creates a team and sets a
        person's team and reports_to, then both are saved."""
        # given
        lead = Person.objects.create(gitlab_user_id=1, name="Lead")
        dev = Person.objects.create(gitlab_user_id=2, name="Dev")
        delegate = Person.objects.create(gitlab_user_id=3, name="Deputy")

        # when
        self.client.post(
            "/admin/reviews/team/add/",
            {
                "name": "Web",
                "lead": lead.pk,
                "delegate": delegate.pk,
                "weekly_urgent_limit": "3",
            },
        )
        team = Team.objects.get(name="Web")
        self.client.post(
            f"/admin/reviews/person/{dev.pk}/change/",
            person_form(dev, team=team.pk, reports_to=lead.pk),
        )

        # then
        dev.refresh_from_db()
        self.assertEqual(
            (team.lead, team.delegate, team.weekly_urgent_limit), (lead, delegate, 3)
        )
        self.assertEqual((dev.team, dev.reports_to), (team, lead))
        with self.subTest("a person who leads a team cannot be deleted"):
            self.client.post(
                f"/admin/reviews/person/{lead.pk}/delete/", {"post": "yes"}
            )

            self.assertTrue(Person.objects.filter(pk=lead.pk).exists())

    def test_urg_2_admin_rejects_a_reporting_loop_and_names_it(self) -> None:
        """URG-2: Given A reports to B, when an admin sets B's reports_to to B or
        to A, then the form is rejected with an error naming the loop."""
        # given
        a = Person.objects.create(gitlab_user_id=1, name="Alice")
        b = Person.objects.create(gitlab_user_id=2, name="Bob", reports_to=a)
        url = f"/admin/reviews/person/{b.pk}/change/"

        # when
        to_self = self.client.post(url, person_form(b, reports_to=b.pk))
        a_to_b = self.client.post(
            f"/admin/reviews/person/{a.pk}/change/", person_form(a, reports_to=b.pk)
        )

        # then
        self.assertContains(to_self, "Reporting loop: Bob reports to Bob.")
        self.assertContains(
            a_to_b, "Reporting loop: Alice reports to Bob reports to Alice."
        )
        a.refresh_from_db()
        self.assertIsNone(a.reports_to)
        with self.subTest("a loop higher up the chain does not hang the save"):
            # given X and Y report to each other (two concurrent admin edits)
            x = Person.objects.create(gitlab_user_id=10, name="X")
            y = Person.objects.create(gitlab_user_id=11, name="Y", reports_to=x)
            Person.objects.filter(pk=x.pk).update(reports_to=y)
            z = Person.objects.create(gitlab_user_id=12, name="Z")

            # when Z is set to report to X
            self.client.post(
                f"/admin/reviews/person/{z.pk}/change/", person_form(z, reports_to=x.pk)
            )

            # then the save ends and is accepted: the loop does not pass through Z
            z.refresh_from_db()
            self.assertEqual(z.reports_to, x)

    def test_urg_3_admin_rejects_a_delegate_who_is_the_lead(self) -> None:
        """URG-3: Given a person, when an admin saves a team whose delegate is its
        lead, then the form is rejected and no team is saved."""
        # given
        lead = Person.objects.create(gitlab_user_id=1, name="Lead")

        # when
        response = self.client.post(
            "/admin/reviews/team/add/",
            {"name": "Web", "lead": lead.pk, "delegate": lead.pk},
        )

        # then
        self.assertContains(response, "The delegate cannot be the lead.")
        self.assertFalse(Team.objects.exists())
