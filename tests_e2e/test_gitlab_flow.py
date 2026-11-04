import httpx
from django.core.management import call_command
from django.test import LiveServerTestCase, override_settings

from gitlab_mock.testing import running_mock
from integrations.adapters import get_gitlab_gateway
from integrations.models import OutboxCall
from reviews.models import (
    CodeArea,
    MergeRequest,
    MergeRequestState,
    OwnerRole,
    Ownership,
    Person,
)

TOKEN = "glpat-e2e-token"
SECRET = "e2e-webhook-secret"
PROJECT_ID = 7
AUTHOR_ID = 11
BOT_ID = 99
ADMIN_ID = 1
API_REVIEWER_ID = 21
WEB_REVIEWER_ID = 22
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


def added_file(path: str) -> dict:
    return {
        "old_path": path,
        "new_path": path,
        "diff": "@@ -0,0 +1 @@\n+change\n",
        "renamed_file": False,
        "too_large": False,
        "collapsed": False,
    }


class GitLabFlowTest(LiveServerTestCase):
    def setUp(self) -> None:
        get_gitlab_gateway.cache_clear()
        self.addCleanup(get_gitlab_gateway.cache_clear)
        Person.objects.create(gitlab_user_id=AUTHOR_ID, name="Author")
        for name, user_id, pattern in (
            ("api", API_REVIEWER_ID, "api/*"),
            ("web", WEB_REVIEWER_ID, "web/*"),
        ):
            person = Person.objects.create(
                gitlab_user_id=user_id, name=name, working_days=EVERY_DAY
            )
            area = CodeArea.objects.create(
                name=name, path_patterns=[pattern], size_limit_lines=500
            )
            Ownership.objects.create(person=person, area=area, role=OwnerRole.PRIMARY)

    def test_approved_merge_request_gets_reviewers_in_the_mock(self) -> None:
        """GLI-12: Given an MR opened in the mock GitLab with the AI Reviewer
        as reviewer and diffs in two areas, when the AI Reviewer approves it
        and the worker runs, then the MR in the mock lists the AI Reviewer and
        both booked reviewers."""
        with running_mock(
            TOKEN, f"{self.live_server_url}/webhooks/gitlab/", SECRET
        ) as base:
            with override_settings(
                DEBUG=True,
                GITLAB_URL=base,
                GITLAB_TOKEN=TOKEN,
                GITLAB_WEBHOOK_TOKEN=SECRET,
                RMO_GITLAB_GATEWAY="integrations.gitlab.GitLabRestGateway",
                RMO_AI_REVIEWER_GITLAB_USER_ID=BOT_ID,
                RMO_INBOX_CLAIM_GRACE_SECONDS=0,
            ):
                # given
                get_gitlab_gateway.cache_clear()
                httpx.post(
                    f"{base}/_mock/mrs",
                    json={
                        "project_id": PROJECT_ID,
                        "author_id": AUTHOR_ID,
                        "reviewer_ids": [BOT_ID],
                        "diffs": [added_file("api/a.py"), added_file("web/b.ts")],
                    },
                    timeout=30,
                ).raise_for_status()
                call_command("run_worker", "--once")  # the MR enters the AI gate
                httpx.post(
                    f"{base}/_mock/mrs/{PROJECT_ID}/1/reviewers/{BOT_ID}/state",
                    json={"state": "approved"},
                    timeout=30,
                ).raise_for_status()

                # when
                call_command("run_worker", "--once")

                # then
                state = httpx.get(f"{base}/_mock/state", timeout=30).json()
                reviewers = state["merge_requests"][0]["reviewer_states"]
                self.assertEqual(
                    sorted(entry["user"]["id"] for entry in reviewers),
                    [API_REVIEWER_ID, WEB_REVIEWER_ID, BOT_ID],
                )

                with self.subTest("booked reviewers approve"):
                    # given
                    self.assertEqual(
                        MergeRequest.objects.get(iid=1).state,
                        MergeRequestState.IN_REVIEW,
                    )

                    # when
                    for reviewer_id in (API_REVIEWER_ID, WEB_REVIEWER_ID):
                        httpx.post(
                            f"{base}/_mock/mrs/{PROJECT_ID}/1/reviewers/{reviewer_id}/state",
                            json={"state": "approved"},
                            timeout=30,
                        ).raise_for_status()
                    call_command("run_worker", "--once")

                    # then
                    self.assertEqual(
                        MergeRequest.objects.get(iid=1).state,
                        MergeRequestState.APPROVED,
                    )

    def test_reconciliation_books_merge_request_without_webhooks(self) -> None:
        """GLI-13: Given the mock GitLab sends no webhooks, when an MR is opened
        and approved by the AI Reviewer and the worker reconciles, then the MR
        lists the AI Reviewer and both booked reviewers; and when the AI Reviewer
        asked for changes, the author pushed and the AI Reviewer approved the new
        commits, the next reconciliation books that MR too; and when the AI Reviewer
        asked for changes and approves later with no push, that MR is booked too."""
        with running_mock(
            TOKEN, f"{self.live_server_url}/webhooks/gitlab/", SECRET
        ) as base:
            with override_settings(
                DEBUG=True,
                GITLAB_URL=base,
                GITLAB_TOKEN=TOKEN,
                GITLAB_WEBHOOK_TOKEN=SECRET,
                RMO_GITLAB_GATEWAY="integrations.gitlab.GitLabRestGateway",
                RMO_AI_REVIEWER_GITLAB_USER_ID=BOT_ID,
                RMO_GITLAB_PROJECT_IDS=[PROJECT_ID],
                RMO_INBOX_CLAIM_GRACE_SECONDS=0,
            ):
                # given
                get_gitlab_gateway.cache_clear()
                httpx.post(
                    f"{base}/_mock/webhooks", json={"enabled": False}, timeout=30
                ).raise_for_status()
                diffs = [added_file("api/a.py"), added_file("web/b.ts")]
                for _ in range(2):
                    httpx.post(
                        f"{base}/_mock/mrs",
                        json={
                            "project_id": PROJECT_ID,
                            "author_id": AUTHOR_ID,
                            "reviewer_ids": [BOT_ID],
                            "diffs": diffs,
                        },
                        timeout=30,
                    ).raise_for_status()
                self.set_bot_state(base, 1, "approved")

                # when
                call_command("run_worker", "--once")

                # then
                state = httpx.get(f"{base}/_mock/state", timeout=30).json()
                reviewers = state["merge_requests"][0]["reviewer_states"]
                self.assertEqual(
                    sorted(entry["user"]["id"] for entry in reviewers),
                    [API_REVIEWER_ID, WEB_REVIEWER_ID, BOT_ID],
                )

                with self.subTest("author pushes after a change request"):
                    # given
                    self.set_bot_state(base, 2, "requested_changes")
                    call_command("run_worker", "--once")  # MR 2 goes to its author
                    self.assertEqual(
                        MergeRequest.objects.get(iid=2).state,
                        MergeRequestState.WAITING_ON_AUTHOR,
                    )
                    httpx.post(
                        f"{base}/_mock/mrs/{PROJECT_ID}/2/push",
                        json={"diffs": diffs},
                        timeout=30,
                    ).raise_for_status()
                    self.set_bot_state(base, 2, "approved")

                    # when
                    call_command("run_worker", "--once")

                    # then
                    self.assertIn(
                        MergeRequest.objects.get(iid=2).state,
                        (
                            MergeRequestState.AWAITING_BOOKING,
                            MergeRequestState.IN_REVIEW,
                        ),
                    )

                with self.subTest("late approval with no push"):
                    # given
                    httpx.post(
                        f"{base}/_mock/mrs",
                        json={
                            "project_id": PROJECT_ID,
                            "author_id": AUTHOR_ID,
                            "reviewer_ids": [BOT_ID],
                            "diffs": diffs,
                        },
                        timeout=30,
                    ).raise_for_status()
                    call_command("run_worker", "--once")  # MR 3 is known
                    self.set_bot_state(base, 3, "requested_changes")
                    call_command("run_worker", "--once")
                    self.assertEqual(
                        MergeRequest.objects.get(iid=3).state,
                        MergeRequestState.WAITING_ON_AUTHOR,
                    )
                    self.set_bot_state(base, 3, "approved")

                    # when
                    call_command("run_worker", "--once")

                    # then
                    self.assertIn(
                        MergeRequest.objects.get(iid=3).state,
                        (
                            MergeRequestState.AWAITING_BOOKING,
                            MergeRequestState.IN_REVIEW,
                        ),
                    )

    def test_merge_by_an_approver_is_recorded_and_alerts_the_admin(self) -> None:
        """End to end for the separation-of-duties check: Given an MR approved
        by both booked reviewers in the mock GitLab, when one of the approvers
        merges it there and the worker runs, then the MR is merged, the merge
        is a violation by an approver, and an admin alert names the approver."""
        Person.objects.create(gitlab_user_id=ADMIN_ID, name="Admin", is_admin=True)
        with running_mock(
            TOKEN, f"{self.live_server_url}/webhooks/gitlab/", SECRET
        ) as base:
            with override_settings(
                DEBUG=True,
                GITLAB_URL=base,
                GITLAB_TOKEN=TOKEN,
                GITLAB_WEBHOOK_TOKEN=SECRET,
                RMO_GITLAB_GATEWAY="integrations.gitlab.GitLabRestGateway",
                RMO_AI_REVIEWER_GITLAB_USER_ID=BOT_ID,
                RMO_INBOX_CLAIM_GRACE_SECONDS=0,
            ):
                # given
                get_gitlab_gateway.cache_clear()
                httpx.post(
                    f"{base}/_mock/mrs",
                    json={
                        "project_id": PROJECT_ID,
                        "author_id": AUTHOR_ID,
                        "reviewer_ids": [BOT_ID],
                        "diffs": [added_file("api/a.py"), added_file("web/b.ts")],
                    },
                    timeout=30,
                ).raise_for_status()
                call_command("run_worker", "--once")
                self.set_bot_state(base, 1, "approved")
                call_command("run_worker", "--once")
                for reviewer_id in (API_REVIEWER_ID, WEB_REVIEWER_ID):
                    httpx.post(
                        f"{base}/_mock/mrs/{PROJECT_ID}/1/reviewers/{reviewer_id}/state",
                        json={"state": "approved"},
                        timeout=30,
                    ).raise_for_status()
                call_command("run_worker", "--once")
                self.assertEqual(
                    MergeRequest.objects.get(iid=1).state,
                    MergeRequestState.APPROVED,
                )

                # when
                httpx.post(
                    f"{base}/_mock/mrs/{PROJECT_ID}/1/merge",
                    json={"user_id": API_REVIEWER_ID},
                    timeout=30,
                ).raise_for_status()
                call_command("run_worker", "--once")

                # then
                self.assertEqual(
                    MergeRequest.objects.get(iid=1).state, MergeRequestState.MERGED
                )
                self.assertEqual(
                    OutboxCall.objects.filter(
                        kind="notify",
                        payload__gitlab_user_ids=[ADMIN_ID],
                        payload__text__icontains="merged by api. Role: approver.",
                    ).count(),
                    1,
                )

    def set_bot_state(self, base: str, iid: int, new_state: str) -> None:
        httpx.post(
            f"{base}/_mock/mrs/{PROJECT_ID}/{iid}/reviewers/{BOT_ID}/state",
            json={"state": new_state},
            timeout=30,
        ).raise_for_status()
