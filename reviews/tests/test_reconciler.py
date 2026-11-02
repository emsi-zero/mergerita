from collections.abc import Callable
from datetime import UTC, datetime
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from integrations.adapters import MergeRequestPage, MergeRequestSummary
from integrations.fakes import FakeGitLabGateway
from integrations.gitlab import GitLabError
from integrations.models import InboxEvent, OutboxCall
from reviews import reconciler
from reviews.lifecycle import handle_event, parse_gitlab_time
from reviews.models import MergeRequest, MergeRequestState, Person, ReconcileCursor
from reviews.reconciler import RECONCILE_LOCK_KEY, reconcile_all
from reviews.worker import process_next_event


def summary(iid: int, updated_at: str, sha: str = "a" * 40) -> MergeRequestSummary:
    return MergeRequestSummary(
        iid=iid, updated_at=updated_at, author_id=11, title=f"MR {iid}", sha=sha
    )


def use_fake_gateway(test: SimpleTestCase) -> FakeGitLabGateway:
    """Make the reconciler use a fake gateway for the rest of the test."""
    gateway = FakeGitLabGateway()
    patcher = mock.patch.object(reconciler, "get_gitlab_gateway", return_value=gateway)
    patcher.start()
    test.addCleanup(patcher.stop)
    return gateway


def serve_like_gitlab(
    gateway: FakeGitLabGateway,
    merge_requests: list[MergeRequestSummary],
    page_size: int,
    after_each_call: Callable[[], None] = lambda: None,
) -> None:
    """List `merge_requests` as GitLab does: updated_after filter, oldest update first, paged."""

    def list_open(
        project_id: int,  # pylint: disable=unused-argument
        updated_after: str | None,
        page: int,
    ):
        since = parse_gitlab_time(updated_after) if updated_after else None
        ordered = sorted(
            (
                mr
                for mr in merge_requests
                if since is None or parse_gitlab_time(mr.updated_at) >= since
            ),
            key=lambda mr: mr.updated_at,
        )
        start = (page - 1) * page_size
        chunk = ordered[start : start + page_size]
        after_each_call()
        return MergeRequestPage(
            chunk, page + 1 if start + page_size < len(ordered) else None
        )

    gateway.list_open_merge_requests = list_open  # type: ignore[method-assign]


def crowded_merge_requests(count: int) -> list[MergeRequestSummary]:
    """`count` MRs updated 0.1 s apart from 2026-11-02T10:00:00Z."""
    return [
        summary(iid, f"2026-11-02T10:00:{iid // 10:02d}.{iid % 10}00Z")
        for iid in range(1, count + 1)
    ]


def four_merge_requests() -> list[MergeRequestSummary]:
    return [
        summary(1, "2026-11-02T10:00:00.000Z"),
        summary(2, "2026-11-02T10:10:00.000Z"),
        summary(3, "2026-11-02T10:20:00.000Z"),
        summary(4, "2026-11-02T10:30:00.000Z"),
    ]


@override_settings(RMO_GITLAB_PROJECT_IDS=[7], RMO_INBOX_CLAIM_GRACE_SECONDS=0)
class ReconcilerTest(TestCase):
    def setUp(self) -> None:
        self.author = Person.objects.create(gitlab_user_id=11, name="Author")
        self.gateway = use_fake_gateway(self)

    def test_reconcile_stores_one_event_per_mr_and_advances_cursor(self) -> None:
        """GLI-8: Given two open MRs updated since the cursor, when reconciliation
        runs twice, then one inbox event per MR and updated_at is stored, and
        the cursor is the last updated_at; given the list changes order between
        two calls, then no MR is skipped; given a known MR with the same sha, then
        no versions call is made for it; given over 100 MRs in the lag window, then
        all are stored; given no version has the listed sha, then the event has
        no push time."""
        # given
        self.gateway.open_merge_requests[7] = MergeRequestPage(
            [
                summary(1, "2026-11-02T10:00:00.000Z"),
                summary(2, "2026-11-02T10:05:00.000Z"),
            ],
            next_page=None,
        )

        # when
        reconcile_all()
        reconcile_all()

        # then
        events = InboxEvent.objects.filter(source="reconcile").order_by("event_uuid")
        self.assertEqual(
            [event.event_uuid for event in events],
            ["7:1:2026-11-02T10:00:00.000Z", "7:2:2026-11-02T10:05:00.000Z"],
        )
        self.assertEqual(events[0].mr_key, "7:1")
        self.assertEqual(events[0].payload["object_attributes"]["action"], "reconcile")
        self.assertEqual(
            ReconcileCursor.objects.get(project_id=7).updated_after,
            datetime(2026, 11, 2, 10, 5, tzinfo=UTC),
        )

        with self.subTest("an MR updated between two list calls is not skipped"):
            # given: the first MR is updated after the first call and moves to the end
            merge_requests = four_merge_requests()
            serve_like_gitlab(
                self.gateway,
                merge_requests,
                page_size=2,
                after_each_call=lambda: merge_requests.__setitem__(
                    0, summary(1, "2026-11-02T10:40:00.000Z")
                ),
            )

            # when
            with self.settings(RMO_GITLAB_PROJECT_IDS=[5]):
                reconcile_all()

            # then
            self.assertEqual(
                sorted(
                    InboxEvent.objects.filter(
                        source="reconcile", mr_key__startswith="5:"
                    ).values_list("event_uuid", flat=True)
                ),
                [
                    "5:1:2026-11-02T10:00:00.000Z",
                    "5:1:2026-11-02T10:40:00.000Z",
                    "5:2:2026-11-02T10:10:00.000Z",
                    "5:3:2026-11-02T10:20:00.000Z",
                    "5:4:2026-11-02T10:30:00.000Z",
                ],
            )

        with self.subTest("versions are read only for unknown or changed MRs"):
            # given: MR 1 has the listed sha, MR 2 a different one, MR 3 is unknown
            for iid, stored_sha in ((1, "a" * 40), (2, "c" * 40)):
                MergeRequest.objects.create(
                    project_id=9,
                    iid=iid,
                    author=self.author,
                    title="MR",
                    head_sha=stored_sha,
                )
            serve_like_gitlab(
                self.gateway,
                [
                    summary(1, "2026-11-02T10:00:00.000Z", sha="a" * 40),
                    summary(2, "2026-11-02T10:01:00.000Z", sha="b" * 40),
                    summary(3, "2026-11-02T10:02:00.000Z"),
                ],
                page_size=100,
            )

            # when
            with mock.patch.object(
                self.gateway, "get_version_time", return_value=None
            ) as versions:
                with self.settings(RMO_GITLAB_PROJECT_IDS=[9]):
                    reconcile_all()

            # then
            self.assertEqual(
                [call.args for call in versions.call_args_list],
                [(9, 2, "b" * 40), (9, 3, "a" * 40)],
            )

        with self.subTest("a window of over 100 MRs does not hold the run back"):
            # given: 101 MRs 0.1 s apart, all inside the lag window of the cursor
            ReconcileCursor.objects.create(
                project_id=4, updated_after=datetime(2026, 11, 2, 10, 0, 10, tzinfo=UTC)
            )
            serve_like_gitlab(self.gateway, crowded_merge_requests(101), page_size=100)

            # when
            with self.settings(RMO_GITLAB_PROJECT_IDS=[4]):
                with self.assertNoLogs("reviews.reconciler", level="ERROR"):
                    reconcile_all()

            # then
            self.assertEqual(
                InboxEvent.objects.filter(mr_key__startswith="4:").count(), 101
            )

        with self.subTest("the push time is the version of the listed sha"):
            # given: GitLab has a version for another sha only (a newer push)
            self.gateway.version_times[(6, 1, "z" * 40)] = datetime(
                2026, 11, 2, 9, 0, tzinfo=UTC
            )
            serve_like_gitlab(
                self.gateway,
                [summary(1, "2026-11-02T10:00:00.000Z", sha="a" * 40)],
                page_size=100,
            )

            # when
            with self.settings(RMO_GITLAB_PROJECT_IDS=[6]):
                reconcile_all()

            # then
            attributes = InboxEvent.objects.get(mr_key="6:1").payload[
                "object_attributes"
            ]
            self.assertNotIn("version_created_at", attributes)

    def test_reconcile_creates_unknown_merge_request_like_open(self) -> None:
        """GLI-9: Given an open MR the orchestrator does not know, when
        reconciliation runs and the worker handles the event, then the MR
        exists in awaiting_ai_review with its head sha, entered the AI gate at
        its newest diff version, and a check is queued; a webhook that claims the
        reconcile action creates nothing."""
        # given
        self.gateway.open_merge_requests[7] = MergeRequestPage(
            [summary(3, "2026-11-02T10:00:00.000Z", sha="b" * 40)], next_page=None
        )
        self.gateway.version_times[(7, 3, "b" * 40)] = datetime(
            2026, 11, 2, 9, 30, 0, 250000, tzinfo=UTC
        )

        # when
        reconcile_all()
        process_next_event()

        # then
        merge_request = MergeRequest.objects.get(project_id=7, iid=3)
        self.assertEqual(merge_request.state, MergeRequestState.AWAITING_AI_REVIEW)
        self.assertEqual(merge_request.head_sha, "b" * 40)
        self.assertEqual(
            merge_request.ai_gate_entered_at,
            datetime(2026, 11, 2, 9, 30, 0, 250000, tzinfo=UTC),
        )
        self.assertEqual(OutboxCall.objects.filter(kind="check_ai_review").count(), 1)

        with self.subTest("the reconcile action from a webhook is ignored"):
            # given
            payload = {
                "project": {"id": 7},
                "object_attributes": {"iid": 4, "action": "reconcile", "author_id": 11},
            }

            # when
            handle_event(
                InboxEvent(
                    source="gitlab", event_kind="Merge Request Hook", payload=payload
                )
            )

            # then
            self.assertFalse(MergeRequest.objects.filter(iid=4).exists())


@override_settings(RMO_GITLAB_PROJECT_IDS=[7, 8])
class ReconcilerFailureTest(TestCase):
    def test_gitlab_error_keeps_cursor_and_moves_to_next_project(self) -> None:
        """GLI-11: Given a call fails for project 7 (a GitLab error, or any other
        error) and works for project 8, when reconciliation runs, then the cursor
        of project 7 is unchanged, the error is logged, and project 8 is still
        read; given a later page or a versions call fails (INV-2), then the cursor
        is the last updated_at of the pages stored before."""
        for name, error in (
            ("gitlab_error", GitLabError("GET /projects/7/merge_requests -> 500")),
            ("unexpected_error", KeyError("sha")),
        ):
            with self.subTest(name=name):
                # given
                gateway = use_fake_gateway(self)
                stored = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
                ReconcileCursor.objects.update_or_create(
                    project_id=7, defaults={"updated_after": stored}
                )
                healthy = gateway.list_open_merge_requests
                gateway.open_merge_requests[8] = MergeRequestPage(
                    [summary(1, "2026-11-02T10:00:00.000Z")], next_page=None
                )

                def list_open(
                    project_id, updated_after, page, error=error, healthy=healthy
                ):
                    if project_id == 7:
                        raise error
                    return healthy(project_id, updated_after, page)

                gateway.list_open_merge_requests = list_open  # type: ignore[method-assign]

                # when
                with self.assertLogs("reviews.reconciler", level="ERROR") as logs:
                    reconcile_all()

                # then
                self.assertIn("project 7", logs.output[0])
                self.assertEqual(
                    ReconcileCursor.objects.get(project_id=7).updated_after, stored
                )
                self.assertEqual(
                    InboxEvent.objects.filter(
                        mr_key="8:1", payload__object_attributes__sha="a" * 40
                    ).count(),
                    1,
                )
                InboxEvent.objects.all().delete()

        for name, project_id, fails_on_list in (
            ("later_page_fails", 3, True),
            ("versions_call_fails", 4, False),
        ):
            with self.subTest(name=name):
                # given: the first page of two MRs is stored, the second step fails
                gateway = use_fake_gateway(self)
                calls = []
                serve_like_gitlab(
                    gateway,
                    four_merge_requests(),
                    page_size=2,
                    after_each_call=lambda calls=calls: calls.append(1),
                )
                served = gateway.list_open_merge_requests

                def list_open_failing(  # pylint: disable=cell-var-from-loop
                    pid, updated_after, page
                ):
                    if fails_on_list and len(calls) == 1:
                        raise GitLabError("GET /projects/3/merge_requests -> 500")
                    return served(pid, updated_after, page)

                def versions(pid, iid, sha):  # pylint: disable=unused-argument
                    if iid == 3:
                        raise GitLabError("GET versions -> 500")

                gateway.list_open_merge_requests = list_open_failing  # type: ignore[method-assign]
                gateway.get_version_time = versions  # type: ignore[method-assign]

                # when
                with self.settings(RMO_GITLAB_PROJECT_IDS=[project_id]):
                    with self.assertLogs("reviews.reconciler", level="ERROR"):
                        reconcile_all()

                # then
                self.assertEqual(
                    ReconcileCursor.objects.get(project_id=project_id).updated_after,
                    datetime(2026, 11, 2, 10, 10, tzinfo=UTC),
                )


@override_settings(RMO_GITLAB_PROJECT_IDS=[7])
class ReconcilerLockTest(TransactionTestCase):
    def test_run_is_skipped_while_another_connection_holds_the_lock(self) -> None:
        """GLI-10: Given a second connection holds the reconcile lock, when
        reconciliation runs, then it reads nothing from GitLab."""
        # given
        gateway = use_fake_gateway(self)
        other = connection.copy()
        self.addCleanup(other.close)
        with other.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [RECONCILE_LOCK_KEY])

        # when
        with mock.patch.object(gateway, "list_open_merge_requests") as listing:
            reconcile_all()

        # then
        listing.assert_not_called()
