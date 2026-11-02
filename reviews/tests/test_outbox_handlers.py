import threading
import time
from unittest import mock

import httpx
from django.db import connection
from django.test import TransactionTestCase, override_settings

from gitlab_mock.testing import running_mock
from integrations.gitlab import GitLabRestGateway
from reviews import outbox_handlers

TOKEN = "t"
BOT = 99


# pylint: disable-next=abstract-method
class SlowReadGateway(GitLabRestGateway):
    """Pause after each GET, so two unlocked set_reviewer calls would overlap."""

    def _request(self, method, path, deadline, **kwargs):
        response = super()._request(method, path, deadline, **kwargs)
        if method == "GET":
            time.sleep(0.3)
        return response


def _payload(reviewer_id: int) -> dict:
    return {"project_id": 7, "iid": 1, "reviewer_gitlab_user_id": reviewer_id}


class _HoldingGateway:  # pylint: disable=too-few-public-methods
    """A gateway whose set_reviewer waits, so the handler keeps its lock."""

    def __init__(self, holding: threading.Event, release: threading.Event) -> None:
        self.holding = holding
        self.release = release

    def set_reviewer(self, *_args: int) -> None:
        self.holding.set()
        self.release.wait(timeout=10)


class SetReviewerHandlerTest(TransactionTestCase):
    """The set_reviewer outbox handler against the mock GitLab."""

    def test_parallel_handlers_keep_every_reviewer(self) -> None:
        """Concurrency (GLI 3, the reviewer merge): Given an MR with the AI
        bot as reviewer, when two workers set different reviewers on it at
        the same time, then a busy call is retried and the MR in GitLab holds the
        bot and both new reviewers."""
        # given
        with running_mock(TOKEN) as base:
            httpx.post(
                f"{base}/_mock/mrs",
                json={"project_id": 7, "author_id": 5, "reviewer_ids": [BOT]},
                timeout=10,
            ).raise_for_status()
            with override_settings(GITLAB_URL=base, GITLAB_TOKEN=TOKEN, DEBUG=True):
                gateway = SlowReadGateway()
            busy: list[int] = []

            def run(reviewer_id: int) -> None:
                try:
                    outbox_handlers.set_reviewer(_payload(reviewer_id))
                except outbox_handlers.ReviewerLockBusy:
                    busy.append(reviewer_id)
                finally:
                    connection.close()

            threads = [threading.Thread(target=run, args=(uid,)) for uid in (21, 22)]

            # when
            with mock.patch.object(
                outbox_handlers, "get_gitlab_gateway", return_value=gateway
            ):
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                for reviewer_id in busy:  # the outbox retries a busy call
                    outbox_handlers.set_reviewer(_payload(reviewer_id))

            # then
            mr = httpx.get(
                f"{base}/api/v4/projects/7/merge_requests/1",
                headers={"PRIVATE-TOKEN": TOKEN},
                timeout=10,
            ).json()
        self.assertEqual(sorted(r["id"] for r in mr["reviewers"]), [21, 22, BOT])

        with self.subTest("busy lock sends no request"):
            # given
            holding = threading.Event()
            release = threading.Event()
            holder = _HoldingGateway(holding, release)
            idle = mock.Mock()

            def hold() -> None:
                with mock.patch.object(
                    outbox_handlers, "get_gitlab_gateway", return_value=holder
                ):
                    outbox_handlers.set_reviewer(_payload(21))
                connection.close()

            thread = threading.Thread(target=hold)
            thread.start()
            holding.wait(timeout=10)

            # when / then
            with mock.patch.object(
                outbox_handlers, "get_gitlab_gateway", return_value=idle
            ):
                with self.assertRaises(outbox_handlers.ReviewerLockBusy):
                    outbox_handlers.set_reviewer(_payload(22))
            release.set()
            thread.join()
            idle.set_reviewer.assert_not_called()
