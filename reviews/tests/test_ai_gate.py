from datetime import UTC, datetime, timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from audit.models import AuditEvent
from integrations import outbox
from integrations.adapters import Changes
from integrations.fakes import FakeGitLabGateway
from integrations.models import InboxEvent, OutboxCall
from reviews import ai_gate, area_resolver
from reviews.lifecycle import handle_event
from reviews.models import (
    CodeArea,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
)

BOT_ID = 99
AWAITING = MergeRequestState.AWAITING_AI_REVIEW
WAITING = MergeRequestState.WAITING_ON_AUTHOR
BOOKING = MergeRequestState.AWAITING_BOOKING


def merge_request_hook(
    iid: int, action: str, user_id: int = 11, **attributes: object
) -> InboxEvent:
    return InboxEvent(
        event_kind="Merge Request Hook",
        payload={
            "user": {"id": user_id},
            "project": {"id": 7},
            "object_attributes": {"iid": iid, "action": action, **attributes},
        },
    )


def note_hook(iid: int, noteable_type: str, user_id: int = 11) -> InboxEvent:
    return InboxEvent(
        event_kind="Note Hook",
        payload={
            "user": {"id": user_id},
            "project": {"id": 7},
            "object_attributes": {"noteable_type": noteable_type},
            "merge_request": {"iid": iid},
        },
    )


def hours_from_now(hours: int) -> datetime:
    return timezone.now() + timedelta(hours=hours)


@override_settings(RMO_AI_REVIEWER_GITLAB_USER_ID=BOT_ID)
class AiGateTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")
        self.gateway = FakeGitLabGateway()
        for module in (ai_gate, area_resolver):
            patcher = mock.patch.object(
                module, "get_gitlab_gateway", return_value=self.gateway
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def create_merge_request(self, iid: int, state: str) -> MergeRequest:
        return MergeRequest.objects.create(
            project_id=7,
            iid=iid,
            author=self.author,
            title="MR",
            state=state,
            ai_gate_entered_at=hours_from_now(-24),
        )

    def queued_checks(self, merge_request: MergeRequest) -> int:
        return OutboxCall.objects.filter(
            kind="check_ai_review", payload={"merge_request_id": merge_request.pk}
        ).count()

    def seed_review(
        self,
        iid: int,
        reviewer_state: str,
        verdict_time: datetime | None = None,
        comment_time: datetime | None = None,
    ) -> None:
        self.gateway.reviewer_states[(7, iid, BOT_ID)] = reviewer_state
        if verdict_time is not None:
            self.gateway.latest_verdict_times[(7, iid, BOT_ID)] = verdict_time
        if comment_time is not None:
            self.gateway.latest_comment_times[(7, iid, BOT_ID)] = comment_time

    def test_hooks_queue_one_ai_review_check(self) -> None:
        """GLI-5: Given an MR awaiting AI review, when a Merge Request Hook or
        a Note Hook on an MR arrives, then one AI-review check is queued; a
        Note Hook on another noteable queues none; given an MR waiting on the
        author, a hook from the AI Reviewer queues a check and a hook from
        anyone else queues none; two hooks queue two checks that give one
        transition and one area resolution."""
        for iid, name, state, event, expected_checks in (
            (3, "mr_hook", AWAITING, merge_request_hook(3, "approval"), 1),
            (4, "note_hook", AWAITING, note_hook(4, "MergeRequest"), 1),
            (5, "note_on_issue", AWAITING, note_hook(5, "Issue"), 0),
            (
                6,
                "bot_mr_hook_waiting",
                WAITING,
                merge_request_hook(6, "approval", BOT_ID),
                1,
            ),
            (7, "bot_note_waiting", WAITING, note_hook(7, "MergeRequest", BOT_ID), 1),
            (
                8,
                "other_hook_waiting",
                WAITING,
                merge_request_hook(8, "approval", 11),
                0,
            ),
        ):
            with self.subTest(name=name):
                # given
                merge_request = self.create_merge_request(iid, state)

                # when
                handle_event(event)

                # then
                self.assertEqual(self.queued_checks(merge_request), expected_checks)

        with self.subTest(name="repeated_hooks"):
            # given
            merge_request = self.create_merge_request(9, AWAITING)
            self.seed_review(9, "approved", hours_from_now(1))

            # when
            handle_event(merge_request_hook(9, "approval"))
            handle_event(note_hook(9, "MergeRequest"))
            for call in OutboxCall.objects.filter(
                kind="check_ai_review", payload={"merge_request_id": merge_request.pk}
            ):
                ai_gate.check_ai_review(call.payload)

            # then
            self.assertEqual(self.queued_checks(merge_request), 2)
            self.assertEqual(
                AuditEvent.objects.filter(
                    action="mr.ai_review_verdict", subject_id=str(merge_request.pk)
                ).count(),
                1,
            )
            self.assertEqual(
                OutboxCall.objects.filter(
                    kind="resolve_areas", payload={"merge_request_id": merge_request.pk}
                ).count(),
                1,
            )

    def test_ai_reviewer_state_moves_merge_request(self) -> None:
        """GLI-6: Given an MR awaiting AI review, when the check runs, then a
        fresh approved awaits booking and queues area resolution; a fresh
        requested_changes or reviewed waits on the author and notifies the
        author; given an MR waiting on the author, a fresh approved awaits
        booking and a fresh requested_changes changes nothing; a verdict event
        that is not newer than the gate entry (for example the bot only wrote a
        plain comment after the old verdict, or a reviewed state with a stale
        comment), a missing event, or any other state leaves the MR unchanged;
        a reviewed state is fresh by the comment, not by a verdict note."""
        fresh, stale = hours_from_now(1), hours_from_now(-1)
        for iid, start, reviewer_state, verdict, comment, expected, kind in (
            (3, AWAITING, "approved", fresh, None, BOOKING, "resolve_areas"),
            (4, AWAITING, "requested_changes", fresh, None, WAITING, "notify"),
            (5, AWAITING, "reviewed", None, fresh, WAITING, "notify"),
            (6, AWAITING, "unreviewed", fresh, fresh, AWAITING, None),
            (7, AWAITING, None, fresh, fresh, AWAITING, None),
            (8, AWAITING, "approved", None, fresh, AWAITING, None),
            (9, WAITING, "approved", fresh, None, BOOKING, "resolve_areas"),
            (10, WAITING, "requested_changes", fresh, None, WAITING, None),
            (11, WAITING, "approved", None, None, WAITING, None),
            (15, WAITING, "reviewed", None, fresh, WAITING, None),
        ):
            with self.subTest(start=start, reviewer_state=reviewer_state, iid=iid):
                # given
                merge_request = self.create_merge_request(iid, start)
                if reviewer_state is not None:
                    self.seed_review(iid, reviewer_state, verdict, comment)

                # when
                ai_gate.check_ai_review({"merge_request_id": merge_request.pk})

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, expected)
                self.assertEqual(
                    list(OutboxCall.objects.values_list("kind", flat=True)),
                    [kind] if kind else [],
                )
                OutboxCall.objects.all().delete()

        for iid, start, reviewer_state, verdict, comment in (
            (12, AWAITING, "approved", stale, None),
            (13, AWAITING, "requested_changes", stale, None),
            (14, WAITING, "approved", stale, None),
            (16, AWAITING, "reviewed", None, stale),
            (17, AWAITING, "reviewed", fresh, stale),
        ):
            with self.subTest(stale=reviewer_state, start=start, iid=iid):
                # given: the bot's latest verdict event is older than the gate entry
                merge_request = self.create_merge_request(iid, start)
                merge_request.ai_gate_entered_at = hours_from_now(0)
                merge_request.save()
                self.seed_review(iid, reviewer_state, verdict, comment)

                # when
                ai_gate.check_ai_review({"merge_request_id": merge_request.pk})

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, start)
                self.assertFalse(OutboxCall.objects.exists())

    def test_new_commits_return_merge_request_to_ai_gate(self) -> None:
        """GLI-7: Given an MR waiting on the author or awaiting AI review, when
        a Merge Request Hook reports new commits, then the gate entry time is
        reset and the MR awaits AI review again, with an audit event and one
        queued check when it was waiting; a hook without new commits, or an MR
        in another state, changes nothing; the entry time is the update time
        of the hook payload (ISO 8601 or "UTC" format, a whole second counts
        as its end), never moves back, and is set under a row lock."""
        long_ago = hours_from_now(-23)
        for iid, name, state, attributes, expected_state, reset, audits in (
            (3, "waiting", WAITING, {"oldrev": "abc"}, AWAITING, True, 1),
            (4, "awaiting", AWAITING, {"oldrev": "abc"}, AWAITING, True, 0),
            (5, "no_new_commits", WAITING, {}, WAITING, False, 0),
            (
                6,
                "in_review",
                MergeRequestState.IN_REVIEW,
                {"oldrev": "abc"},
                MergeRequestState.IN_REVIEW,
                False,
                0,
            ),
        ):
            with self.subTest(name=name):
                # given
                merge_request = self.create_merge_request(iid, state)

                # when
                handle_event(merge_request_hook(iid, "update", **attributes))

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, expected_state)
                self.assertEqual(merge_request.ai_gate_entered_at > long_ago, reset)
                self.assertEqual(self.queued_checks(merge_request), int(reset))
                self.assertEqual(
                    AuditEvent.objects.filter(
                        action="mr.new_commits", subject_id=str(merge_request.pk)
                    ).count(),
                    audits,
                )

        for iid, updated_at, expected in (
            (
                7,
                "2026-11-02T09:45:57.123Z",
                datetime(2026, 11, 2, 9, 45, 57, 123000, tzinfo=UTC),
            ),
            (
                8,
                "2026-11-02 09:45:57 UTC",
                datetime(2026, 11, 2, 9, 45, 57, 999999, tzinfo=UTC),
            ),
        ):
            with self.subTest(updated_at=updated_at):
                # given
                merge_request = self.create_merge_request(iid, WAITING)
                merge_request.ai_gate_entered_at = datetime(2026, 1, 1, tzinfo=UTC)
                merge_request.save()

                # when
                handle_event(
                    merge_request_hook(
                        iid, "update", oldrev="abc", updated_at=updated_at
                    )
                )

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.ai_gate_entered_at, expected)

        with self.subTest(name="entry_time_never_moves_back"):
            # given
            merge_request = self.create_merge_request(9, AWAITING)
            merge_request.ai_gate_entered_at = datetime(2030, 1, 1, tzinfo=UTC)
            merge_request.save()

            # when
            handle_event(
                merge_request_hook(
                    9, "update", oldrev="abc", updated_at="2026-11-02T09:45:57.123Z"
                )
            )

            # then
            merge_request.refresh_from_db()
            self.assertEqual(
                merge_request.ai_gate_entered_at, datetime(2030, 1, 1, tzinfo=UTC)
            )

        with self.subTest(name="row_lock"):
            # given
            self.create_merge_request(10, WAITING)

            # when
            with CaptureQueriesContext(connection) as queries:
                handle_event(merge_request_hook(10, "update", oldrev="abc"))

            # then
            self.assertTrue(
                any("FOR UPDATE" in q["sql"] for q in queries.captured_queries)
            )

    def test_approval_after_push_is_not_lost_to_the_old_verdict(self) -> None:
        """GLI-14: Given an MR waiting on the author after requested changes,
        when the author pushes and the AI Reviewer then comments, writes its
        verdict note and approves, in any order of comment, verdict note and
        approval hook the orchestrator sees, then the MR awaits booking, and
        it never leaves the AI gate on the way."""
        area = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=500
        )
        Ownership.objects.create(person=self.author, area=area, role=OwnerRole.PRIMARY)
        push_time = hours_from_now(-6).isoformat()
        for iid in (3, 4, 5):
            self.gateway.changes[(7, iid)] = Changes(
                files=["api/a.py"], changed_lines=5
            )

        with self.subTest(name="comment_then_approval"):
            # given
            merge_request = self.create_merge_request(3, WAITING)
            self.seed_review(3, "requested_changes", hours_from_now(-12))

            # when the author pushes, and the bot comments while its state is still old
            handle_event(
                merge_request_hook(3, "update", oldrev="abc", updated_at=push_time)
            )
            outbox.dispatch_due()
            handle_event(note_hook(3, "MergeRequest", BOT_ID))
            outbox.dispatch_due()

            # then
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, AWAITING)

            # when the bot writes its verdict note and approves
            self.seed_review(3, "approved", hours_from_now(-5))
            handle_event(merge_request_hook(3, "approval", BOT_ID))
            outbox.dispatch_due()

            # then
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, BOOKING)

        with self.subTest(name="approval_before_comment"):
            # given
            merge_request = self.create_merge_request(4, WAITING)
            self.seed_review(4, "requested_changes", hours_from_now(-12))
            handle_event(
                merge_request_hook(4, "update", oldrev="abc", updated_at=push_time)
            )
            outbox.dispatch_due()

            # when the approval hook arrives before the comment hook
            self.seed_review(4, "approved", hours_from_now(-5))
            handle_event(merge_request_hook(4, "approval", BOT_ID))
            handle_event(note_hook(4, "MergeRequest", BOT_ID))
            outbox.dispatch_due()

            # then
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, BOOKING)

        with self.subTest(name="bounced_then_approved"):
            # given an MR wrongly bounced to the author
            merge_request = self.create_merge_request(5, WAITING)
            merge_request.ai_gate_entered_at = hours_from_now(-6)
            merge_request.save()
            self.seed_review(5, "approved", hours_from_now(-5))

            # when the bot's approval hook arrives
            handle_event(merge_request_hook(5, "approval", BOT_ID))
            outbox.dispatch_due()

            # then
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, BOOKING)
