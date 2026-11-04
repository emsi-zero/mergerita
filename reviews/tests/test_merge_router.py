import threading
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from audit.models import AuditEvent
from integrations.fakes import FakeGitLabGateway
from integrations.models import OutboxCall
from reviews import booking, coverage, merge_router
from reviews.models import (
    AreaReview,
    AreaReviewState,
    Leave,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    Person,
)

TEHRAN = ZoneInfo("Asia/Tehran")
NOW = datetime(2026, 11, 4, 8, 0, tzinfo=TEHRAN)  # a Wednesday
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def window(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=TEHRAN)


def merge_owner(gitlab_user_id: int, **fields: object) -> Person:
    fields.setdefault("merge_times", ["07:00", "10:00"])
    fields.setdefault("working_days", EVERY_DAY)
    return Person.objects.create(
        gitlab_user_id=gitlab_user_id,
        name=f"P{gitlab_user_id}",
        is_merge_owner=True,
        **fields,
    )


def create_merge_request(author: Person, iid: int, state: str) -> MergeRequest:
    return MergeRequest.objects.create(
        project_id=7,
        iid=iid,
        author=author,
        title=f"MR {iid}",
        state=state,
        ai_gate_entered_at=NOW - timedelta(hours=24),
    )


class MergeRouterTest(TestCase):
    """Merge owner assignment."""

    def setUp(self) -> None:
        self.enterContext(mock.patch("django.utils.timezone.now", return_value=NOW))

    def test_mrg_1_approval_assigns_the_best_eligible_merge_owner_and_window(
        self,
    ) -> None:
        # pylint: disable=too-many-locals
        """MRG-1: Given merge owners that include the author, a reviewer, a
        person on leave and a person at the daily limit, when the last area
        approval makes the MR approved, then the MR goes to the eligible owner
        with the fewest merges in the earliest window at or after now, with a
        reason, a chat message and an audit event."""
        # given
        author = merge_owner(11)
        reviewer = merge_owner(12)
        on_leave = merge_owner(13)
        full = merge_owner(14, daily_merge_limit=1)
        busy = merge_owner(15)
        best = merge_owner(16)
        Leave.objects.create(
            person=on_leave, start_date=NOW.date(), end_date=NOW.date() + timedelta(1)
        )
        for iid, person in ((1, full), (3, busy)):
            MergeAssignment.objects.create(
                merge_request=create_merge_request(
                    author, iid, MergeRequestState.IN_REVIEW
                ),
                merge_owner=person,
                window=window(4, 10),
            )
        merge_request = create_merge_request(author, 2, MergeRequestState.IN_REVIEW)
        AreaReview.objects.create(
            merge_request=merge_request,
            state=AreaReviewState.BOOKED,
            reviewer=reviewer,
        )
        gateway = FakeGitLabGateway()
        gateway.reviewer_states[(7, 2, reviewer.gitlab_user_id)] = "approved"
        gateway.latest_verdict_times[(7, 2, reviewer.gitlab_user_id)] = NOW
        self.enterContext(
            mock.patch.object(coverage, "get_gitlab_gateway", return_value=gateway)
        )

        # when
        coverage.check_coverage({"merge_request_id": merge_request.pk})

        # then
        assignment = MergeAssignment.objects.get(merge_request=merge_request)
        merge_request.refresh_from_db()
        self.assertEqual(merge_request.state, MergeRequestState.APPROVED)
        self.assertEqual(assignment.merge_owner, best)
        self.assertEqual(assignment.window, window(4, 10))
        self.assertEqual(
            assignment.reason,
            "Earliest window 10:00 on 2026-11-04; 0 of 5 merges that day",
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="merge.assigned", subject_id=str(assignment.pk)
            ).exists()
        )
        self.assertEqual(
            OutboxCall.objects.get(
                kind="notify", payload__gitlab_user_ids=[best.gitlab_user_id]
            ).payload["text"],
            "Merge !2 MR 2 in your 10:00 window on 2026-11-04.",
        )

        # more cases, each on a clean slate: owner fields, ids on leave today,
        # existing assignments today, horizon, expected (owner, day, hour)
        plain_author = Person.objects.create(gitlab_user_id=90, name="Plain")
        plain_request = create_merge_request(plain_author, 99, MergeRequestState.MERGED)
        for name, owners, leave_ids, preload, horizon, expected in (
            (
                "non_working_day_is_skipped",
                {31: {"working_days": [3]}},
                [],
                {},
                10,
                (31, 5, 7),
            ),
            ("fits_on_last_day", {32: {"working_days": [4]}}, [], {}, 3, (32, 6, 7)),
            (
                "earlier_day_beats_lower_count",
                {33: {}, 34: {}},
                [34],
                {33: 2},
                10,
                (33, 4, 10),
            ),
            (
                "equal_count_earlier_window_wins",
                {35: {"merge_times": ["10:00"]}, 36: {"merge_times": ["09:00"]}},
                [],
                {},
                10,
                (36, 4, 9),
            ),
            (
                "equal_window_lower_id_wins",
                {37: {"merge_times": ["10:00"]}, 38: {"merge_times": ["10:00"]}},
                [],
                {},
                10,
                (37, 4, 10),
            ),
        ):
            with self.subTest(name), self.settings(RMO_BOOKING_HORIZON_DAYS=horizon):
                # given
                MergeAssignment.objects.all().delete()
                Person.objects.filter(is_merge_owner=True).delete()
                people = {
                    pk: merge_owner(pk, **fields) for pk, fields in owners.items()
                }
                Leave.objects.bulk_create(
                    Leave(person=people[pk], start_date=NOW.date(), end_date=NOW.date())
                    for pk in leave_ids
                )
                MergeAssignment.objects.bulk_create(
                    MergeAssignment(
                        merge_request=plain_request,
                        merge_owner=people[pk],
                        window=window(4, 10),
                        merged_at=window(4, 10),
                    )
                    for pk, count in preload.items()
                    for _ in range(count)
                )
                merge_request = create_merge_request(
                    plain_author, 100 + expected[0], MergeRequestState.APPROVED
                )

                # when
                with transaction.atomic():
                    booking.lock_booking()
                    assignment = merge_router.assign_merge(merge_request)

                # then
                self.assertEqual(
                    (assignment.merge_owner.gitlab_user_id, assignment.window),
                    (expected[0], window(expected[1], expected[2])),
                )
        with (
            self.subTest("one_day_short_gives_no_fit"),
            self.settings(RMO_BOOKING_HORIZON_DAYS=2),
        ):
            # given
            MergeAssignment.objects.all().delete()
            Person.objects.filter(is_merge_owner=True).delete()
            merge_owner(32, working_days=[4])
            merge_request = create_merge_request(
                plain_author, 200, MergeRequestState.APPROVED
            )

            # when
            with transaction.atomic():
                booking.lock_booking()
                assignment = merge_router.assign_merge(merge_request)

            # then
            self.assertIsNone(assignment)

    def test_mrg_2_no_fit_leaves_the_mr_approved_and_alerts_the_admin(self) -> None:
        """MRG-2: Given no merge owner who fits within the horizon, when an MR
        becomes approved, then it stays approved without an assignment and an
        admin alert is queued."""
        # given
        admin = Person.objects.create(gitlab_user_id=1, name="Admin", is_admin=True)
        author = merge_owner(11)
        merge_request = create_merge_request(author, 1, MergeRequestState.APPROVED)

        # when
        with transaction.atomic():
            booking.lock_booking()
            assignment = merge_router.assign_merge(merge_request)

        # then
        merge_request.refresh_from_db()
        self.assertIsNone(assignment)
        self.assertEqual(merge_request.state, MergeRequestState.APPROVED)
        self.assertFalse(MergeAssignment.objects.exists())
        self.assertEqual(
            OutboxCall.objects.get(kind="notify").payload["gitlab_user_ids"],
            [admin.gitlab_user_id],
        )
        self.assertTrue(AuditEvent.objects.filter(action="merge.unassigned").exists())


@override_settings(RMO_BOOKING_HORIZON_DAYS=1)
class MergeRouterConcurrencyTest(TransactionTestCase):
    """Merge owner assignment under concurrency."""

    def test_mrg_3_concurrent_assignments_never_exceed_the_daily_limit(self) -> None:
        """MRG-3: Given one merge owner with a daily limit of 1 and two MRs,
        when both are assigned at the same time, then exactly one is assigned."""
        # given
        author = Person.objects.create(gitlab_user_id=11, name="Author")
        merge_owner(21, merge_times=["10:00"], daily_merge_limit=1)
        merge_request_ids = [
            create_merge_request(author, iid, MergeRequestState.APPROVED).pk
            for iid in (1, 2)
        ]
        barrier = threading.Barrier(2)
        results = []

        def assign(merge_request_id: int) -> None:
            try:
                merge_request = MergeRequest.objects.get(pk=merge_request_id)
                barrier.wait()
                with transaction.atomic():
                    booking.lock_booking()
                    results.append(merge_router.assign_merge(merge_request))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=assign, args=(pk,)) for pk in merge_request_ids
        ]

        # when
        with mock.patch("django.utils.timezone.now", return_value=NOW):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        # then
        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual(MergeAssignment.objects.count(), 1)
