from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from audit.models import AuditEvent
from integrations.fakes import FakeGitLabGateway
from integrations.models import InboxEvent, OutboxCall
from reviews import ai_gate, coverage
from reviews.lifecycle import handle_event
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
    ReviewSession,
)

IN_REVIEW = MergeRequestState.IN_REVIEW
BOT_ID = 99
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def hours_from_now(hours: int) -> datetime:
    return timezone.now() + timedelta(hours=hours)


@override_settings(RMO_AI_REVIEWER_GITLAB_USER_ID=BOT_ID)
class CoverageTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")
        self.gateway = FakeGitLabGateway()
        for module in (coverage, ai_gate):
            self.enterContext(
                mock.patch.object(
                    module, "get_gitlab_gateway", return_value=self.gateway
                )
            )

    def create_merge_request(self, iid: int, state: str) -> MergeRequest:
        return MergeRequest.objects.create(
            state=state,
            title=f"MR {iid}",
            iid=iid,
            project_id=7,
            author=self.author,
            ai_gate_entered_at=hours_from_now(-24),
        )

    def book_reviewer(
        self,
        merge_request: MergeRequest,
        gitlab_user_id: int,
        state: str = AreaReviewState.BOOKED,
    ) -> AreaReview:
        reviewer = Person.objects.create(
            gitlab_user_id=gitlab_user_id,
            name=f"Reviewer {gitlab_user_id}",
            working_days=EVERY_DAY,
        )
        area = CodeArea.objects.create(
            name=f"area {gitlab_user_id}", path_patterns=[], size_limit_lines=9
        )
        Ownership.objects.create(person=reviewer, area=area, role=OwnerRole.PRIMARY)
        area_review = AreaReview.objects.create(
            merge_request=merge_request,
            state=state,
            reviewer=reviewer,
            booked_day=timezone.localdate(),
        )
        area_review.areas.set([area])
        ReviewSession.objects.create(
            area_review=area_review,
            reviewer=reviewer,
            day=timezone.localdate(),
            slot_minutes=30,
        )
        return area_review

    def book_reviewers(
        self, merge_request: MergeRequest, verdicts: list[tuple[str, int]]
    ) -> None:
        """Book one reviewer per verdict (state, hours from now) and seed GitLab."""
        for offset, (reviewer_state, hours) in enumerate(verdicts):
            gitlab_user_id = 200 + merge_request.iid * 10 + offset
            self.book_reviewer(merge_request, gitlab_user_id)
            self.seed_verdict(merge_request.iid, gitlab_user_id, reviewer_state, hours)

    def push_while_in_review(self, iid: int) -> None:
        handle_event(
            InboxEvent(
                event_kind="Merge Request Hook",
                payload={
                    "user": {"id": 11},
                    "project": {"id": 7},
                    "object_attributes": {
                        "iid": iid,
                        "action": "update",
                        "oldrev": "a1",
                        "updated_at": timezone.now().isoformat(),
                        "last_commit": {"id": "b2"},
                    },
                },
            )
        )

    def assert_follow_up(
        self, area_review: AreaReview, gitlab_user_id: int, iid: int
    ) -> None:
        """The area review has a pending continuation session and its reviewer was told once."""
        self.assertEqual(
            list(
                area_review.sessions.filter(outcome="pending").values_list(
                    "is_continuation", flat=True
                )
            ),
            [True],
        )
        self.assertEqual(
            OutboxCall.objects.filter(
                kind="notify",
                payload__gitlab_user_ids=[gitlab_user_id],
                payload__text__istartswith=f"Follow-up review of !{iid} ",
            ).count(),
            1,
        )

    def seed_verdict(
        self, iid: int, gitlab_user_id: int, reviewer_state: str | None, hours: int
    ) -> None:
        self.gateway.reviewer_states[(7, iid, gitlab_user_id)] = reviewer_state
        self.gateway.latest_verdict_times[(7, iid, gitlab_user_id)] = hours_from_now(
            hours
        )

    def test_rvo_6_hooks_and_reconcile_queue_one_coverage_check_in_review(
        self,
    ) -> None:
        """RVO-6: Given an MR in review, when a Merge Request Hook, a Note Hook
        or a reconciliation event arrives, then one coverage check is queued;
        given an MR in another state, then none is queued.
        """
        for iid, name, state, event_kind, source, attributes, expected in (
            (
                1,
                "mr_hook",
                IN_REVIEW,
                "Merge Request Hook",
                "webhook",
                {"action": "approval"},
                1,
            ),
            (
                2,
                "note_hook",
                IN_REVIEW,
                "Note Hook",
                "webhook",
                {"noteable_type": "MergeRequest"},
                1,
            ),
            (
                3,
                "reconcile",
                IN_REVIEW,
                "Merge Request Hook",
                "reconcile",
                {"action": "reconcile"},
                1,
            ),
            (
                4,
                "not_in_review",
                MergeRequestState.AWAITING_AI_REVIEW,
                "Merge Request Hook",
                "webhook",
                {"action": "approval"},
                0,
            ),
        ):
            with self.subTest(name):
                # given
                merge_request = self.create_merge_request(iid, state)
                event = InboxEvent(
                    source=source,
                    event_kind=event_kind,
                    payload={
                        "user": {"id": 11},
                        "project": {"id": 7},
                        "object_attributes": {"iid": iid, **attributes},
                        "merge_request": {"iid": iid},
                    },
                )

                # when
                handle_event(event)

                # then
                self.assertEqual(
                    OutboxCall.objects.filter(
                        kind="check_coverage",
                        payload={"merge_request_id": merge_request.pk},
                    ).count(),
                    expected,
                )

    def test_rvo_7_fresh_verdicts_approve_areas_and_the_mr_or_return_it(
        self,
    ) -> None:
        # pylint: disable=too-many-locals,too-many-statements
        """RVO-7: Given an MR in review with booked area reviews, when the
        coverage check runs, then a fresh approval approves its area, a fresh
        request for changes moves the MR to the author with a notification, every
        area approved approves the MR, and stale or other states change nothing;
        each change writes an audit event.
        """
        for iid, name, verdicts, area_states, mr_state, notified, actions in (
            (
                1,
                "approved",
                [("approved", -1)],
                ["approved"],
                "approved",
                0,
                [
                    "area_review.approved",
                    "review_session.done",
                    "mr.approved",
                    "merge.unassigned",
                ],
            ),
            (
                2,
                "one_of_two",
                [("approved", -1), ("reviewed", -1)],
                ["approved", "booked"],
                "in_review",
                0,
                ["area_review.approved", "review_session.done"],
            ),
            (3, "stale_approval", [("approved", -48)], ["booked"], "in_review", 0, []),
            (4, "other_state", [("reviewed", -1)], ["booked"], "in_review", 0, []),
            (
                5,
                "changes_requested",
                [("requested_changes", -1)],
                ["booked"],
                "waiting_on_author",
                1,
                ["area_review.changes_requested", "mr.changes_requested"],
            ),
        ):
            with self.subTest(name):
                # given
                merge_request = self.create_merge_request(iid, IN_REVIEW)
                self.book_reviewers(merge_request, verdicts)

                started = timezone.now()

                # when
                coverage.check_coverage({"merge_request_id": merge_request.pk})

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, mr_state)
                self.assertEqual(
                    merge_request.approved_at is not None,
                    mr_state == MergeRequestState.APPROVED,
                )
                self.assertEqual(
                    list(
                        merge_request.area_reviews.order_by("pk").values_list(
                            "state", flat=True
                        )
                    ),
                    area_states,
                )
                self.assertEqual(
                    OutboxCall.objects.filter(
                        kind="notify",
                        payload__text__icontains=f"requested changes on !{iid}.",
                    ).count(),
                    notified,
                )
                self.assertEqual(
                    sorted(
                        AuditEvent.objects.filter(created_at__gte=started).values_list(
                            "action", flat=True
                        )
                    ),
                    sorted(actions),
                )
                self.assertEqual(
                    ReviewSession.objects.filter(
                        area_review__merge_request=merge_request, outcome="done"
                    ).count(),
                    actions.count("area_review.approved"),
                )

        with self.subTest("approval revoked in GitLab"):
            # given
            merge_request = self.create_merge_request(10, IN_REVIEW)
            revoked = self.book_reviewer(merge_request, 301, AreaReviewState.APPROVED)
            waiting = self.book_reviewer(merge_request, 302)
            self.seed_verdict(10, 301, "reviewed", -1)
            self.seed_verdict(10, 302, "approved", -1)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            merge_request.refresh_from_db()
            revoked.refresh_from_db()
            waiting.refresh_from_db()
            self.assertEqual(
                (merge_request.state, revoked.state, waiting.state),
                (IN_REVIEW, AreaReviewState.BOOKED, AreaReviewState.APPROVED),
            )
            self.assertEqual(
                AuditEvent.objects.filter(action="area_review.approval_reset").count(),
                1,
            )
            self.assert_follow_up(revoked, 301, 10)

        with self.subTest("approval reset by a push while in review"):
            # given
            merge_request = self.create_merge_request(14, IN_REVIEW)
            pushed = self.book_reviewer(merge_request, 341, AreaReviewState.APPROVED)
            self.seed_verdict(14, 341, "approved", -1)
            self.push_while_in_review(14)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            pushed.refresh_from_db()
            self.assertEqual(pushed.state, AreaReviewState.BOOKED)
            self.assert_follow_up(pushed, 341, 14)

        for iid, name, other_areas, mr_state in (
            (15, "only_area", 0, "approved"),
            (16, "another_area_open", 1, "in_review"),
        ):
            with self.subTest(f"reviewer approves after asking for changes: {name}"):
                # given
                merge_request = self.create_merge_request(iid, IN_REVIEW)
                self.book_reviewer(merge_request, 350 + iid)
                for extra in range(other_areas):
                    self.book_reviewer(merge_request, 380 + iid + extra)
                self.seed_verdict(iid, 350 + iid, "requested_changes", -2)
                coverage.check_coverage({"merge_request_id": merge_request.pk})
                self.seed_verdict(iid, 350 + iid, "approved", 1)

                # when
                coverage.check_coverage({"merge_request_id": merge_request.pk})

                # then
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, mr_state)

        with self.subTest("another reviewer's approval survives a resume"):
            # given
            merge_request = self.create_merge_request(17, IN_REVIEW)
            approved = self.book_reviewer(merge_request, 371, AreaReviewState.APPROVED)
            asking = self.book_reviewer(merge_request, 372)
            self.seed_verdict(17, 371, "approved", -1)
            self.seed_verdict(17, 372, "requested_changes", -2)
            coverage.check_coverage({"merge_request_id": merge_request.pk})
            self.seed_verdict(17, 372, "approved", 1)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            approved.refresh_from_db()
            asking.refresh_from_db()
            self.assertEqual(
                (approved.state, approved.sessions.count(), asking.state),
                (AreaReviewState.APPROVED, 1, AreaReviewState.APPROVED),
            )

        with self.subTest("a second change request keeps the MR waiting"):
            # given
            merge_request = self.create_merge_request(18, IN_REVIEW)
            first = self.book_reviewer(merge_request, 381)
            second = self.book_reviewer(merge_request, 382)
            self.seed_verdict(18, 381, "requested_changes", -3)
            coverage.check_coverage({"merge_request_id": merge_request.pk})
            self.seed_verdict(18, 382, "requested_changes", -2)
            coverage.check_coverage({"merge_request_id": merge_request.pk})
            self.seed_verdict(18, 381, "approved", 1)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            merge_request.refresh_from_db()
            first.refresh_from_db()
            second.refresh_from_db()
            self.assertEqual(
                (merge_request.state, first.changes_requested_at is None),
                (MergeRequestState.WAITING_ON_AUTHOR, False),
            )
            self.assertIsNotNone(second.changes_requested_at)

        with self.subTest("approval after a push and an AI change request"):
            # given
            merge_request = self.create_merge_request(19, IN_REVIEW)
            asking = self.book_reviewer(merge_request, 391)
            self.seed_verdict(19, 391, "requested_changes", -2)
            coverage.check_coverage({"merge_request_id": merge_request.pk})
            self.push_while_in_review(19)
            self.seed_verdict(19, BOT_ID, "requested_changes", 1)
            ai_gate.check_ai_review({"merge_request_id": merge_request.pk})
            self.seed_verdict(19, 391, "approved", 2)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            merge_request.refresh_from_db()
            asking.refresh_from_db()
            self.assertEqual(
                (merge_request.state, asking.state),
                (MergeRequestState.WAITING_ON_AUTHOR, AreaReviewState.BOOKED),
            )

        with self.subTest("change request older than the latest push"):
            # given
            merge_request = self.create_merge_request(20, IN_REVIEW)
            asking = self.book_reviewer(merge_request, 401)
            self.seed_verdict(20, 401, "requested_changes", -2)
            coverage.check_coverage({"merge_request_id": merge_request.pk})
            merge_request.refresh_from_db()
            merge_request.head_sha_at = hours_from_now(-1)
            merge_request.save(update_fields=["head_sha_at"])
            self.seed_verdict(20, 401, "approved", 2)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            merge_request.refresh_from_db()
            self.assertEqual(merge_request.state, MergeRequestState.WAITING_ON_AUTHOR)

        with self.subTest("approval from before a push"):
            # given
            merge_request = self.create_merge_request(11, IN_REVIEW)
            area_review = self.book_reviewer(merge_request, 311)
            self.seed_verdict(11, 311, "approved", -1)
            self.push_while_in_review(11)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            area_review.refresh_from_db()
            self.assertEqual(area_review.state, AreaReviewState.BOOKED)

        for iid, name, human_hours, bot_hours in (
            (12, "change_request_not_undone_by_ai_reviewer", -1, -2),
            (13, "orchestrator_clock_far_behind_gitlab", 10, 5),
        ):
            with self.subTest(name):
                # given
                merge_request = self.create_merge_request(iid, IN_REVIEW)
                requester = self.book_reviewer(merge_request, 320 + iid)
                self.seed_verdict(iid, 320 + iid, "requested_changes", human_hours)
                self.seed_verdict(iid, BOT_ID, "approved", bot_hours)
                coverage.check_coverage({"merge_request_id": merge_request.pk})

                # when
                handle_event(
                    InboxEvent(
                        source="reconcile",
                        event_kind="Merge Request Hook",
                        payload={
                            "user": {"id": 11},
                            "project": {"id": 7},
                            "object_attributes": {"iid": iid, "action": "reconcile"},
                        },
                    )
                )
                ai_gate.check_ai_review({"merge_request_id": merge_request.pk})

                # then
                merge_request.refresh_from_db()
                requester.refresh_from_db()
                self.assertEqual(
                    merge_request.state, MergeRequestState.WAITING_ON_AUTHOR
                )
                self.assertEqual(
                    requester.changes_requested_at,
                    self.gateway.latest_verdict_times[(7, iid, 320 + iid)],
                )
                self.assertFalse(
                    OutboxCall.objects.filter(
                        kind="resolve_areas",
                        payload={"merge_request_id": merge_request.pk},
                    ).exists()
                )
                self.assertEqual(
                    OutboxCall.objects.filter(
                        kind="notify",
                        payload__text__icontains=f"requested changes on !{iid}.",
                    ).count(),
                    1,
                )

    def test_mrg_11_lost_approval_returns_the_mr_and_cancels_the_merge(self) -> None:
        """MRG-11: Given an approved MR with an open merge assignment, when a
        hook arrives, then a coverage check is queued; and when an area review
        has lost its fresh approval, then the MR returns to review with the
        follow-up of RVO-7, the assignment is cancelled and the merge owner is
        told; and when the MR is merged, then no check is queued."""
        # given
        merge_request = self.create_merge_request(20, MergeRequestState.APPROVED)
        lost = self.book_reviewer(merge_request, 401, AreaReviewState.APPROVED)
        self.seed_verdict(20, 401, "reviewed", -1)
        merge_owner = Person.objects.create(
            gitlab_user_id=402,
            name="Merge owner",
            is_merge_owner=True,
            merge_times=["10:00"],
            working_days=EVERY_DAY,
        )
        assignment = MergeAssignment.objects.create(
            merge_request=merge_request,
            merge_owner=merge_owner,
            window=hours_from_now(2),
        )
        hook = InboxEvent(
            source="webhook",
            event_kind="Merge Request Hook",
            payload={
                "user": {"id": 11},
                "project": {"id": 7},
                "object_attributes": {"iid": 20, "action": "approval"},
            },
        )

        # when
        handle_event(hook)
        queued = OutboxCall.objects.filter(kind="check_coverage").count()
        coverage.check_coverage({"merge_request_id": merge_request.pk})

        # then
        merge_request.refresh_from_db()
        assignment.refresh_from_db()
        lost.refresh_from_db()
        self.assertEqual(queued, 1)
        self.assertEqual(merge_request.state, IN_REVIEW)
        self.assertEqual(lost.state, AreaReviewState.BOOKED)
        self.assertIsNotNone(assignment.cancelled_at)
        self.assertTrue(AuditEvent.objects.filter(action="merge.cancelled").exists())
        self.assert_follow_up(lost, 401, 20)
        self.assertEqual(
            OutboxCall.objects.filter(
                kind="notify",
                payload__gitlab_user_ids=[402],
                payload__text__startswith="Do not merge !20 ",
            ).count(),
            1,
        )

        with self.subTest("a later approval assigns again"):
            # given
            self.seed_verdict(20, 401, "approved", 1)

            # when
            coverage.check_coverage({"merge_request_id": merge_request.pk})

            # then
            merge_request.refresh_from_db()
            assignments = merge_request.merge_assignments
            self.assertEqual(merge_request.state, MergeRequestState.APPROVED)
            self.assertEqual(assignments.filter(cancelled_at__isnull=False).count(), 1)
            self.assertEqual(assignments.filter(cancelled_at__isnull=True).count(), 1)

        with self.subTest("merged MR is not checked"):
            # given
            merged = self.create_merge_request(21, MergeRequestState.MERGED)
            hook.payload["object_attributes"]["iid"] = 21

            # when
            handle_event(hook)

            # then
            self.assertFalse(
                OutboxCall.objects.filter(
                    kind="check_coverage", payload={"merge_request_id": merged.pk}
                ).exists()
            )
