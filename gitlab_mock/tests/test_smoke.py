"""Smoke tests for the mock: one per route group. They need no Django database."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from gitlab_mock.testing import running_mock

HEADERS = {"PRIVATE-TOKEN": "t"}
BOT = {"id": 99, "username": "ai-bot", "name": "AI Bot"}


def _diff(path: str) -> dict[str, Any]:
    return {
        "old_path": path,
        "new_path": path,
        "diff": "@@ -1 +1 @@\n-a\n+b\n",
        "new_file": False,
        "renamed_file": False,
        "deleted_file": False,
        "too_large": False,
        "collapsed": False,
    }


def _open_mr(base: str, project_id: int, **extra: object) -> dict[str, Any]:
    body = {"project_id": project_id, "author_id": 5, **extra}
    return httpx.post(f"{base}/_mock/mrs", json=body, timeout=10).json()


class _Receiver(BaseHTTPRequestHandler):
    """Collect the webhooks that the mock sends."""

    received: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        length = int(self.headers["Content-Length"])
        # Keep the case-insensitive header object: header names are
        # case-insensitive, and httpx 0.28 sends "X-Gitlab-Event-Uuid".
        self.received.append(
            {"headers": self.headers, "body": json.loads(self.rfile.read(length))}
        )
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


class MockSmokeTests(unittest.TestCase):
    """Smoke tests for the mock routes."""

    def test_diffs_are_paged_with_next_page_header(self) -> None:
        """Diffs are paged and X-Next-Page points to the next page."""
        with running_mock("t") as base:
            diffs = [_diff(f"f{i}.py") for i in range(3)]
            _open_mr(base, 1, diffs=diffs)
            url = f"{base}/api/v4/projects/1/merge_requests/1/diffs"

            first = httpx.get(url, params={"per_page": 2}, headers=HEADERS, timeout=10)
            last = httpx.get(
                url, params={"per_page": 2, "page": 2}, headers=HEADERS, timeout=10
            )

        self.assertEqual(len(first.json()), 2)
        self.assertEqual(first.headers["X-Next-Page"], "2")
        self.assertEqual(first.headers["X-Per-Page"], "2")
        self.assertEqual(last.headers["X-Page"], "2")
        self.assertEqual(last.headers["X-Next-Page"], "")
        self.assertEqual(last.json()[0]["new_path"], "f2.py")

    def test_reviewer_put_keeps_existing_and_wrong_token_is_401(self) -> None:
        """A reviewer PUT keeps existing states and a wrong token gets 401."""
        with running_mock("t") as base:
            _open_mr(base, 1)
            mr_url = f"{base}/api/v4/projects/1/merge_requests/1"
            httpx.put(mr_url, json={"reviewer_ids": [7]}, headers=HEADERS, timeout=10)

            httpx.put(
                mr_url, json={"reviewer_ids": [7, 99]}, headers=HEADERS, timeout=10
            )
            reviewers = httpx.get(f"{mr_url}/reviewers", headers=HEADERS, timeout=10)
            unauthorized = httpx.get(mr_url, headers={"PRIVATE-TOKEN": "x"}, timeout=10)

        self.assertEqual([r["user"]["id"] for r in reviewers.json()], [7, 99])
        self.assertEqual(reviewers.json()[0]["state"], "unreviewed")
        self.assertEqual(unauthorized.status_code, 401)

    def test_list_filters_by_state_and_updated_after(self) -> None:
        """The MR list filters by state and updated_after."""
        with running_mock("t") as base:
            _open_mr(base, 1)
            second = _open_mr(base, 1)
            url = f"{base}/api/v4/projects/1/merge_requests"

            listed = httpx.get(
                url,
                params={
                    "state": "opened",
                    "updated_after": second["updated_at"],
                    "order_by": "updated_at",
                    "sort": "asc",
                },
                headers=HEADERS,
                timeout=10,
            )
            closed = httpx.get(
                url, params={"state": "merged"}, headers=HEADERS, timeout=10
            )

        self.assertEqual([m["iid"] for m in listed.json()], [2])
        self.assertEqual(closed.json(), [])

    def test_webhook_is_delivered_with_gitlab_headers(self) -> None:
        """A webhook reaches a local receiver with the GitLab headers."""
        _Receiver.received = []
        receiver = HTTPServer(("127.0.0.1", 0), _Receiver)
        thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(receiver.server_close)
        self.addCleanup(receiver.shutdown)
        hook_url = f"http://127.0.0.1:{receiver.server_address[1]}/hook"

        with running_mock("t", hook_url, "secret") as base:
            _open_mr(base, 1, reviewer_ids=[BOT["id"]])
            httpx.post(
                f"{base}/_mock/mrs/1/1/reviewers/99/state",
                json={"state": "approved"},
                timeout=10,
            ).raise_for_status()
            reviewers = httpx.get(
                f"{base}/api/v4/projects/1/merge_requests/1/reviewers",
                headers=HEADERS,
                timeout=10,
            )

        self.assertEqual(reviewers.json()[0]["state"], "approved")
        opened = _Receiver.received[0]
        approved = _Receiver.received[1]
        self.assertEqual(opened["headers"]["X-Gitlab-Token"], "secret")
        self.assertEqual(opened["headers"]["X-Gitlab-Event"], "Merge Request Hook")
        self.assertNotEqual(
            opened["headers"]["X-Gitlab-Event-UUID"],
            approved["headers"]["X-Gitlab-Event-UUID"],
        )
        self.assertEqual(opened["body"]["object_attributes"]["action"], "open")
        self.assertEqual(approved["body"]["object_attributes"]["action"], "approval")
        self.assertEqual(opened["body"]["reviewers"][0]["id"], 99)
        self.assertEqual(approved["body"]["user"]["id"], 99)
        self.assertEqual(approved["body"]["project"]["id"], 1)
