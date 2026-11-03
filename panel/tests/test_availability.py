from datetime import date

from django.test import TestCase

from audit.models import AuditEvent
from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_person
from reviews.models import Leave


class AvailabilityTests(TestCase):
    def setUp(self) -> None:
        self.me = create_person(1, "Me", "me")
        self.other = create_person(2, "Other", "other")
        panel_login(self.client, self.me.user)

    def test_dvp_7_working_days_saved_and_empty_selection_rejected(self):
        """DVP-7: Given my working days,
        when I save a selection, then it is stored;
        when I save none, then it is rejected with an error.
        """
        response = self.client.post(
            "/panel/availability/days/", {"days": ["0", "2", "6"]}
        )
        self.me.refresh_from_db()
        saved_days = self.me.working_days

        empty = self.client.post("/panel/availability/days/", {})
        self.me.refresh_from_db()

        self.assertRedirects(
            response, "/panel/availability/", fetch_redirect_response=False
        )
        self.assertEqual(saved_days, [0, 2, 6])
        self.assertEqual(empty.status_code, 400)
        self.assertContains(empty, "Select at least one working day.", status_code=400)
        self.assertEqual(self.me.working_days, [0, 2, 6])

    def test_dvp_8_budget_accepts_only_configured_choices(self):
        """DVP-8: Given the budget choices,
        when I save a choice, then it is stored;
        when I save any other value, then it is rejected.
        """
        response = self.client.post("/panel/availability/budget/", {"minutes": "180"})
        self.me.refresh_from_db()
        self.assertRedirects(
            response, "/panel/availability/", fetch_redirect_response=False
        )
        self.assertEqual(self.me.daily_budget_minutes, 180)

        for value in ["0", "10000", "abc", ""]:
            with self.subTest(value=value):
                rejected = self.client.post(
                    "/panel/availability/budget/", {"minutes": value}
                )
                self.me.refresh_from_db()

                self.assertEqual(rejected.status_code, 400)
                self.assertEqual(self.me.daily_budget_minutes, 180)

    def test_dvp_9_leave_is_added_for_me_and_deleted_only_when_mine(self):
        """DVP-9: Given my leave and a colleague's leave,
        when I add leave, then it is stored for me and a reversed range is rejected;
        when I delete leave, then only my own is removed and another id is 404.
        """
        theirs = Leave.objects.create(
            person=self.other, start_date=date(2026, 12, 1), end_date=date(2026, 12, 2)
        )

        added = self.client.post(
            "/panel/availability/leave/",
            {"start_date": "2026-11-15", "end_date": "2026-11-16"},
        )
        mine = Leave.objects.get(person=self.me)
        reversed_range = self.client.post(
            "/panel/availability/leave/",
            {"start_date": "2026-11-20", "end_date": "2026-11-19"},
        )
        foreign_delete = self.client.post(
            f"/panel/availability/leave/{theirs.pk}/delete/"
        )
        own_delete = self.client.post(f"/panel/availability/leave/{mine.pk}/delete/")

        self.assertRedirects(
            added, "/panel/availability/", fetch_redirect_response=False
        )
        self.assertEqual(mine.start_date, date(2026, 11, 15))
        self.assertEqual(mine.end_date, date(2026, 11, 16))
        self.assertEqual(reversed_range.status_code, 400)
        self.assertContains(
            reversed_range,
            "The end date must not be before the start date.",
            status_code=400,
        )
        self.assertEqual(foreign_delete.status_code, 404)
        self.assertTrue(Leave.objects.filter(pk=theirs.pk).exists())
        self.assertRedirects(
            own_delete, "/panel/availability/", fetch_redirect_response=False
        )
        self.assertFalse(Leave.objects.filter(person=self.me).exists())

    def test_dvp_10_each_change_writes_one_audit_event(self):
        """DVP-10: Given an availability change,
        when it is saved, then one audit event holds the actor, action,
        and the before and after values.
        """
        self.client.post("/panel/availability/days/", {"days": ["1"]})
        self.client.post("/panel/availability/budget/", {"minutes": "240"})
        self.client.post(
            "/panel/availability/leave/",
            {"start_date": "2026-11-15", "end_date": "2026-11-16"},
        )
        leave = Leave.objects.get(person=self.me)
        self.client.post(f"/panel/availability/leave/{leave.pk}/delete/")

        events = list(
            AuditEvent.objects.order_by("pk").values_list(
                "actor", "action", "before", "after"
            )
        )

        self.assertEqual(
            events,
            [
                (
                    "user:me",
                    "availability.working_days_changed",
                    {"working_days": [5, 6, 0, 1, 2]},
                    {"working_days": [1]},
                ),
                (
                    "user:me",
                    "availability.budget_changed",
                    {"daily_budget_minutes": None},
                    {"daily_budget_minutes": 240},
                ),
                (
                    "user:me",
                    "availability.leave_added",
                    None,
                    {"start_date": "2026-11-15", "end_date": "2026-11-16"},
                ),
                (
                    "user:me",
                    "availability.leave_deleted",
                    {"start_date": "2026-11-15", "end_date": "2026-11-16"},
                    None,
                ),
            ],
        )
