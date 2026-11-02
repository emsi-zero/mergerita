from datetime import date, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from audit.models import AuditEvent
from integrations import outbox
from integrations.adapters import Changes
from integrations.fakes import FakeGitLabGateway
from integrations.inbox import store_event
from reviews import ai_gate, area_resolver, booking
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequest,
    OwnerRole,
    Ownership,
    Person,
)
from reviews.worker import process_next_event

MONDAY = date(2026, 10, 26)


def store_merge_request_event(uuid: str, action: str) -> None:
    payload = {
        "project": {"id": 7},
        "object_attributes": {"iid": 3, "action": action, "author_id": 11},
    }
    store_event("gitlab", uuid, "Merge Request Hook", "7:3", payload)


@override_settings(RMO_INBOX_CLAIM_GRACE_SECONDS=0, RMO_AI_REVIEWER_GITLAB_USER_ID=99)
class AuditTrailTest(TestCase):
    def test_every_state_change_and_booking_writes_an_audit_event(self) -> None:
        """RMO-17: Given an MR that is opened, passes AI review, gets its area
        reviews and is booked, when each step runs, then one audit event exists
        per state change and per booking, with actor, action, subject, before
        and after."""
        # given
        Person.objects.create(gitlab_user_id=11, name="Author")
        reviewer = Person.objects.create(
            gitlab_user_id=21, name="Reviewer", working_days=[0]
        )
        area = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=500
        )
        Ownership.objects.create(person=reviewer, area=area, role=OwnerRole.PRIMARY)
        gateway = FakeGitLabGateway()
        gateway.changes[(7, 3)] = Changes(files=["api/a.py"], changed_lines=9)
        gateway.reviewer_states[(7, 3, 99)] = "approved"
        gateway.latest_verdict_times[(7, 3, 99)] = timezone.now() + timedelta(hours=1)
        for target, name, value in (
            (ai_gate, "get_gitlab_gateway", gateway),
            (area_resolver, "get_gitlab_gateway", gateway),
            (booking.timezone, "localdate", MONDAY),
        ):
            patcher = mock.patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        store_merge_request_event("u-1", "open")
        store_merge_request_event("u-2", "approval")

        # when
        process_next_event()
        process_next_event()
        outbox.dispatch_due()

        # then
        merge_request = MergeRequest.objects.get()
        area_review = AreaReview.objects.get()
        events = AuditEvent.objects.order_by("id")
        self.assertEqual(
            [
                (e.actor, e.action, e.subject_type, e.subject_id, e.before, e.after)
                for e in events
            ],
            [
                (
                    "gitlab",
                    "mr.opened",
                    "reviews.MergeRequest",
                    str(merge_request.pk),
                    None,
                    {"state": "awaiting_ai_review"},
                ),
                (
                    "gitlab",
                    "mr.ai_review_verdict",
                    "reviews.MergeRequest",
                    str(merge_request.pk),
                    {"state": "awaiting_ai_review"},
                    {"state": "awaiting_booking"},
                ),
                (
                    "orchestrator",
                    "area_review.created",
                    "reviews.AreaReview",
                    str(area_review.pk),
                    None,
                    {"state": "unbooked", "areas": [area.pk]},
                ),
                (
                    "orchestrator",
                    "area_review.booked",
                    "reviews.AreaReview",
                    str(area_review.pk),
                    {"state": "unbooked", "reviewer": None, "booked_day": None},
                    {
                        "state": "booked",
                        "reviewer": reviewer.pk,
                        "booked_day": "2026-10-26",
                        "reason": "primary owner; 2026-10-26; 120 of 120 minutes free",
                    },
                ),
                (
                    "orchestrator",
                    "mr.in_review",
                    "reviews.MergeRequest",
                    str(merge_request.pk),
                    {"state": "awaiting_booking"},
                    {"state": "in_review"},
                ),
            ],
        )
