import httpx
from django.core.management import call_command
from django.test import LiveServerTestCase, override_settings

from gitlab_mock.testing import running_mock
from integrations.adapters import get_gitlab_gateway
from reviews.models import CodeArea, OwnerRole, Ownership, Person

TOKEN = "glpat-e2e-token"
SECRET = "e2e-webhook-secret"
PROJECT_ID = 7
AUTHOR_ID = 11
BOT_ID = 99
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
