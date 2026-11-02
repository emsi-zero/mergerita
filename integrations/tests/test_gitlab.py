import logging
import traceback

import httpx
import respx
from django.test import SimpleTestCase, override_settings

from gitlab_mock.testing import running_mock
from integrations.gitlab import GitLabError, GitLabRestGateway

TOKEN = "glpat-secret-value"


def _diff(old_path: str, new_path: str, diff: str, **flags: bool) -> dict:
    return {
        "old_path": old_path,
        "new_path": new_path,
        "diff": diff,
        "renamed_file": old_path != new_path,
        "too_large": False,
        "collapsed": False,
        **flags,
    }


def _open_mr(base: str, **body: object) -> None:
    httpx.post(
        f"{base}/_mock/mrs", json={"project_id": 7, "author_id": 5, **body}, timeout=10
    ).raise_for_status()


def _mock_reviewer_ids(base: str) -> list[int]:
    response = httpx.get(
        f"{base}/api/v4/projects/7/merge_requests/1",
        headers={"PRIVATE-TOKEN": TOKEN},
        timeout=10,
    )
    return [reviewer["id"] for reviewer in response.json()["reviewers"]]


class GitLabRestGatewayTest(SimpleTestCase):
    """Gateway tests against the mock GitLab (real HTTP) and respx (failures)."""

    def test_get_changes_reads_all_pages_and_both_rename_paths(self) -> None:
        """GLI-1: Given an MR with 101 file diffs and a rename, when changes are
        read, then every path of both pages is returned, a rename gives both
        paths, and added plus removed lines are counted; a collapsed or too-large
        diff sets too_large; a repeated next-page header raises GitLabError."""
        # given
        diffs = [
            _diff(f"f{i}.py", f"f{i}.py", "@@ -1 +1 @@\n-a\n+b\n") for i in range(100)
        ]
        diffs.append(_diff("old/name.py", "new/name.py", "@@ -1 +1,2 @@\n a\n+b\n+c\n"))

        with running_mock(TOKEN) as base:
            _open_mr(base, diffs=diffs)
            with override_settings(GITLAB_URL=base, GITLAB_TOKEN=TOKEN, DEBUG=True):
                gateway = GitLabRestGateway()

                # when
                changes = gateway.get_changes(7, 1)

        # then
        self.assertEqual(len(changes.files), 102)
        self.assertEqual(changes.files[-2:], ["new/name.py", "old/name.py"])
        self.assertEqual(changes.changed_lines, 202)
        self.assertFalse(changes.too_large)

        for name, flags in (
            ("collapsed", {"collapsed": True}),
            ("too_large", {"too_large": True}),
        ):
            with self.subTest(name):
                # given
                with running_mock(TOKEN) as base:
                    _open_mr(base, diffs=[_diff("a.py", "a.py", "", **flags)])
                    with override_settings(
                        GITLAB_URL=base, GITLAB_TOKEN=TOKEN, DEBUG=True
                    ):
                        gateway = GitLabRestGateway()

                        # when
                        changes = gateway.get_changes(7, 1)

                # then
                self.assertTrue(changes.too_large)

        with self.subTest("repeated next page"):
            # given
            with respx.mock(base_url="https://gitlab.test/api/v4") as router:
                router.get("/projects/7/merge_requests/1/diffs").respond(
                    200, json=[], headers={"X-Next-Page": "1"}
                )
                with override_settings(
                    GITLAB_URL="https://gitlab.test", GITLAB_TOKEN=TOKEN
                ):
                    gateway = GitLabRestGateway()

                    # when / then
                    with self.assertRaises(GitLabError):
                        gateway.get_changes(7, 1)

    def test_set_reviewer_keeps_existing_reviewers(self) -> None:
        """GLI-3: Given an MR with reviewer 99, when reviewer 21 is set, then the
        MR has both; given 21 is already a reviewer, when set again, then no
        update is sent."""
        # given
        with running_mock(TOKEN) as base:
            _open_mr(base, reviewer_ids=[99])
            with override_settings(GITLAB_URL=base, GITLAB_TOKEN=TOKEN, DEBUG=True):
                gateway = GitLabRestGateway()

                # when
                gateway.set_reviewer(7, 1, 21)

            # then
            self.assertEqual(_mock_reviewer_ids(base), [99, 21])

        # given
        with respx.mock(
            base_url="https://gitlab.test/api/v4", assert_all_called=False
        ) as router:
            router.get("/projects/7/merge_requests/1").respond(
                200, json={"reviewers": [{"id": 99}, {"id": 21}]}
            )
            put = router.put("/projects/7/merge_requests/1").respond(200)
            with override_settings(
                GITLAB_URL="https://gitlab.test", GITLAB_TOKEN=TOKEN
            ):
                gateway = GitLabRestGateway()

                # when
                gateway.set_reviewer(7, 1, 21)

        # then
        self.assertFalse(put.called)

    def test_failed_call_raises_error_without_token(self) -> None:
        """GLI-4: Given GitLab answers 500, times out, or drops the connection,
        when a call is made, then GitLabError is raised, and neither the error
        nor the logs hold the token."""
        cases = {
            "status": httpx.Response(500, text=f"boom {TOKEN}"),
            "timeout": httpx.ReadTimeout(f"slow {TOKEN}"),
            "transport": httpx.ConnectError(f"down {TOKEN}"),
        }
        for name, outcome in cases.items():
            with self.subTest(name):
                # given
                with (
                    respx.mock(base_url="https://gitlab.test/api/v4") as router,
                    override_settings(
                        GITLAB_URL="https://gitlab.test", GITLAB_TOKEN=TOKEN
                    ),
                ):
                    router.get("/projects/7/merge_requests/1/diffs").mock(
                        side_effect=[outcome]
                    )
                    gateway = GitLabRestGateway()

                    # when
                    with self.assertLogs(level=logging.DEBUG) as logs:
                        logging.getLogger("test").debug("start")
                        with self.assertRaises(GitLabError) as raised:
                            gateway.get_changes(7, 1)

                # then
                error = raised.exception
                self.assertNotIn(TOKEN, str(error))
                self.assertNotIn(TOKEN, "".join(logs.output))
                self.assertIsNone(error.__cause__)
                self.assertNotIn(TOKEN, "".join(traceback.format_exception(error)))

        with self.subTest("deadline"):
            # given
            with override_settings(
                GITLAB_URL="https://gitlab.test",
                GITLAB_TOKEN=TOKEN,
                RMO_GITLAB_CALL_DEADLINE_SECONDS=-1,
            ):
                gateway = GitLabRestGateway()

                # when / then
                with self.assertRaisesMessage(GitLabError, "deadline"):
                    gateway.get_changes(7, 1)
