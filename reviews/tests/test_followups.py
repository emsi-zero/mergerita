from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from integrations.adapters import Changes
from integrations.fakes import FakeGitLabGateway
from integrations.models import OutboxCall
from reviews import area_resolver
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
    ReviewSession,
)

EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


class FollowUpTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")
        self.reviewer = Person.objects.create(
            gitlab_user_id=21, name="Reviewer", working_days=EVERY_DAY
        )
        self.area = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=500
        )
        Ownership.objects.create(
            person=self.reviewer, area=self.area, role=OwnerRole.PRIMARY
        )
        self.gateway = FakeGitLabGateway()
        self.gateway.changes[(7, 1)] = Changes(files=["web/other.py"], changed_lines=1)
        patcher = mock.patch.object(
            area_resolver, "get_gitlab_gateway", return_value=self.gateway
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rvo_8_second_ai_pass_books_follow_ups_with_the_same_reviewer(
        self,
    ) -> None:
        """RVO-8: Given an MR with area reviews that passes the AI gate again,
        when areas are resolved, then each area review that is not approved gets a
        continuation session with the same reviewer and a pending session is
        superseded; an area review GitLab still shows as approved is left alone;
        one it no longer shows as approved loses its mark and gets a follow-up;
        and no area review is created.
        """
        for iid, name, review_state, outcome, gitlab_state, expected, verdict_hours in (
            (
                1,
                "booked_pending",
                "booked",
                "pending",
                None,
                ("booked", ["pending", "superseded"], 1),
                1,
            ),
            (
                2,
                "booked_after_done",
                "booked",
                "done",
                None,
                ("booked", ["done", "pending"], 1),
                1,
            ),
            (
                3,
                "approved_still",
                "approved",
                "done",
                "approved",
                ("approved", ["done"], 0),
                1,
            ),
            (
                4,
                "approved_reset",
                "approved",
                "done",
                "reviewed",
                ("booked", ["done", "pending"], 1),
                1,
            ),
            (
                5,
                "approved_older_than_push",
                "approved",
                "done",
                "approved",
                ("booked", ["done", "pending"], 1),
                -48,
            ),
        ):
            with self.subTest(name):
                # given
                merge_request = MergeRequest.objects.create(
                    state=MergeRequestState.AWAITING_BOOKING,
                    title=f"Fix {name}",
                    iid=iid,
                    project_id=7,
                    author=self.author,
                )
                area_review = AreaReview.objects.create(
                    merge_request=merge_request,
                    state=review_state,
                    reviewer=self.reviewer,
                    booked_day=timezone.localdate() - timedelta(days=1),
                    slot_minutes=30,
                )
                area_review.areas.set([self.area])
                ReviewSession.objects.create(
                    area_review=area_review,
                    reviewer=self.reviewer,
                    day=timezone.localdate() - timedelta(days=1),
                    slot_minutes=30,
                    outcome=outcome,
                )
                self.gateway.reviewer_states[(7, iid, 21)] = gitlab_state
                self.gateway.latest_verdict_times[(7, iid, 21)] = (
                    timezone.now() + timedelta(hours=verdict_hours)
                )

                # when
                area_resolver.resolve_areas({"merge_request_id": merge_request.pk})

                # then
                area_review.refresh_from_db()
                self.assertEqual(area_review.state, expected[0])
                self.assertEqual(
                    sorted(area_review.sessions.values_list("outcome", flat=True)),
                    expected[1],
                )
                self.assertEqual(merge_request.area_reviews.count(), 1)
                self.assertEqual(
                    OutboxCall.objects.filter(
                        kind="notify",
                        payload__text__istartswith=f"Follow-up review of !{iid} ",
                    ).count(),
                    expected[2],
                )
                self.assertEqual(
                    area_review.sessions.filter(is_continuation=True).count(),
                    expected[2],
                )
                merge_request.refresh_from_db()
                self.assertEqual(merge_request.state, MergeRequestState.IN_REVIEW)
                self.assertTrue(
                    OutboxCall.objects.filter(
                        payload={"merge_request_id": merge_request.pk},
                        kind="check_coverage",
                    ).exists()
                )

        with self.subTest("unbooked area review and an area review with no fit"):
            # given
            merge_request = MergeRequest.objects.create(
                state=MergeRequestState.AWAITING_BOOKING,
                title="Mixed",
                iid=9,
                project_id=7,
                author=self.author,
            )
            unbooked = AreaReview.objects.create(merge_request=merge_request)
            unbooked.areas.set([self.area])
            stranger = Person.objects.create(gitlab_user_id=22, name="Stranger")
            no_fit = AreaReview.objects.create(
                merge_request=merge_request,
                state="booked",
                reviewer=stranger,
                booked_day=timezone.localdate(),
                slot_minutes=30,
            )
            no_fit.areas.set([self.area])
            ReviewSession.objects.create(
                area_review=no_fit,
                reviewer=stranger,
                day=timezone.localdate(),
                slot_minutes=30,
            )

            # when
            area_resolver.resolve_areas({"merge_request_id": merge_request.pk})

            # then
            unbooked.refresh_from_db()
            no_fit.refresh_from_db()
            merge_request.refresh_from_db()
            self.assertEqual(
                (unbooked.state, unbooked.reviewer, merge_request.state),
                ("booked", self.reviewer, MergeRequestState.IN_REVIEW),
            )
            self.assertTrue(unbooked.sessions.get().is_continuation)
            self.assertEqual(
                OutboxCall.objects.filter(
                    kind="notify",
                    payload__gitlab_user_ids=[21],
                    payload__text__icontains=" 7:9 ",
                ).count(),
                1,
            )
            self.assertFalse(
                OutboxCall.objects.filter(
                    payload__text__istartswith="Follow-up review of !9 "
                ).exists()
            )
            self.assertEqual(no_fit.state, "unbooked")
            self.assertEqual(
                OutboxCall.objects.filter(
                    kind="notify", payload__text__icontains="no reviewer fits"
                ).count(),
                1,
            )
