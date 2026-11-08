import importlib
from datetime import datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from audit.services import record
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeRequest,
    MergeRequestState,
    Person,
    RebookingSettings,
)

TEHRAN = ZoneInfo("Asia/Tehran")
migration = importlib.import_module("reviews.migrations.0013_rebooking_and_watchdog")


def at(when: datetime):
    return mock.patch("django.utils.timezone.now", return_value=when)


def day(number: int, hour: int = 10) -> datetime:
    return datetime(2026, 11, number, hour, 0, tzinfo=TEHRAN)


def create_merge_request(author: Person, iid: int, **fields: object) -> MergeRequest:
    return MergeRequest.objects.create(
        project_id=7, iid=iid, author=author, title=f"MR {iid}", **fields
    )


class StateChangedAtTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")

    def test_rbw_20_state_change_stamps_state_changed_at_and_other_saves_do_not(
        self,
    ) -> None:
        """RBW-20: Given a stored MR, when its state changes by a full save or by
        `update_fields`, then `state_changed_at` is now; when another field
        changes, or the state is set to the same value, it stays."""
        # given
        create_merge_request(self.author, 1, state_changed_at=day(1))

        # when a field other than the state changes
        with at(day(2)):
            merge_request = MergeRequest.objects.get(iid=1)
            merge_request.title = "New title"
            merge_request.state = MergeRequestState.AWAITING_AI_REVIEW
            merge_request.save()
        # then
        self.assertEqual(MergeRequest.objects.get(iid=1).state_changed_at, day(1))

        # when the state changes with a full save
        with at(day(3)):
            merge_request = MergeRequest.objects.get(iid=1)
            merge_request.state = MergeRequestState.NEEDS_TRIAGE
            merge_request.save()
        # then
        self.assertEqual(MergeRequest.objects.get(iid=1).state_changed_at, day(3))

        # when the state changes with update_fields, and the same save repeats
        with at(day(4)):
            merge_request = MergeRequest.objects.get(iid=1)
            merge_request.state = MergeRequestState.AWAITING_BOOKING
            merge_request.save(update_fields=["state"])
        with at(day(5)):
            merge_request.save(update_fields=["state"])
        # then
        self.assertEqual(MergeRequest.objects.get(iid=1).state_changed_at, day(4))


class BackfillTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")

    def test_rbw_21_backfill_sets_times_from_the_audit_log_or_opened_at(self) -> None:
        """RBW-21: Given MRs and unbooked area reviews with and without audit
        events, when the backfill runs, then each time comes from the latest
        matching event, or from `opened_at`."""
        # given
        with_audit = create_merge_request(self.author, 1, opened_at=day(1))
        without_audit = create_merge_request(self.author, 2, opened_at=day(2))
        area_with_event = AreaReview.objects.create(merge_request=with_audit)
        area_with_older_event = AreaReview.objects.create(merge_request=with_audit)
        area_without_event = AreaReview.objects.create(merge_request=without_audit)
        booked = AreaReview.objects.create(
            merge_request=with_audit, state=AreaReviewState.BOOKED
        )
        with at(day(3)):
            record("orchestrator", "mr.in_review", with_audit, None, {"state": "x"})
        with at(day(4)):
            record("orchestrator", "mr.in_review", with_audit, None, {"state": "y"})
        with at(day(5)):
            record("orchestrator", "mr.noted", with_audit, None, {"note": "no state"})
        with at(day(6)):
            record("orchestrator", "area_review.created", area_with_event, None, None)
        with at(day(7)):
            record("orchestrator", "area_review.unbooked", area_with_event, None, None)
        with at(day(8)):
            record(
                "orchestrator", "area_review.created", area_with_older_event, None, None
            )
        with at(day(9)):
            record(
                "orchestrator", "area_review.booked", area_with_older_event, None, None
            )

        # when
        migration.backfill(apps, None)

        # then
        self.assertEqual(
            MergeRequest.objects.get(pk=with_audit.pk).state_changed_at, day(4)
        )
        self.assertEqual(
            MergeRequest.objects.get(pk=without_audit.pk).state_changed_at, day(2)
        )
        self.assertEqual(
            AreaReview.objects.get(pk=area_with_event.pk).unbooked_since, day(7)
        )
        self.assertEqual(
            AreaReview.objects.get(pk=area_with_older_event.pk).unbooked_since, day(8)
        )
        self.assertEqual(
            AreaReview.objects.get(pk=area_without_event.pk).unbooked_since, day(2)
        )
        self.assertIsNone(AreaReview.objects.get(pk=booked.pk).unbooked_since)


class RebookingSettingsFormTest(TestCase):
    def test_rbw_22_settings_form_rejects_thresholds_outside_1_to_60(self) -> None:
        """RBW-22: Given the settings admin form, when a threshold is 0 or 61,
        then the form is invalid; when it is 1 or 60, and the run time is a time
        of day, then it is valid."""
        # given
        request = RequestFactory().get("/")
        request.user = get_user_model().objects.create_superuser("root", password="x")
        form_class = admin.site.get_model_admin(RebookingSettings).get_form(request)

        def form(**changes: object):
            data = {
                "daily_run_time": "07:30",
                "ai_review_threshold_days": 1,
                "triage_threshold_days": 1,
                "stale_threshold_days": 5,
            }
            return form_class(data | changes, instance=RebookingSettings.load())

        for field in (
            "ai_review_threshold_days",
            "triage_threshold_days",
            "stale_threshold_days",
        ):
            with self.subTest(field=field):
                # when / then
                self.assertIn(field, form(**{field: 0}).errors)
                self.assertIn(field, form(**{field: 61}).errors)
                self.assertTrue(form(**{field: 1}).is_valid())
                self.assertTrue(form(**{field: 60}).is_valid())
        self.assertTrue(form(daily_run_time="23:59").is_valid())
        self.assertEqual(RebookingSettings.load().daily_run_time, time(7, 30))

        # given a form loaded before the worker claimed the day
        model_admin = admin.site.get_model_admin(RebookingSettings)
        stale = form(stale_threshold_days=7)
        RebookingSettings.objects.update(last_rebooking_on=day(8).date())
        # when the admin saves it
        self.assertTrue(stale.is_valid())
        model_admin.save_model(request, stale.save(commit=False), stale, change=True)
        # then the new threshold is stored and the claim is kept (INV-1)
        stored = RebookingSettings.load()
        self.assertEqual(stored.stale_threshold_days, 7)
        self.assertEqual(stored.last_rebooking_on, day(8).date())
