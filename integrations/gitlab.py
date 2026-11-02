import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from integrations.adapters import Changes, MergeRequestPage, MergeRequestSummary

PER_PAGE = 100
# Texts of the system notes that GitLab writes for a review. The smoke test must
# verify them on the real instance (spec open question).
APPROVAL_NOTE_TEXT = "approved this merge request"
CHANGES_NOTE_TEXT = "requested changes"
VERDICT_TEXTS = (APPROVAL_NOTE_TEXT, CHANGES_NOTE_TEXT)


class GitLabError(Exception):
    """A GitLab call failed. The message holds the method, path and status only (INV-3)."""


class GitLabRestGateway:
    """GitLab REST v4 gateway. It raises GitLabError on any failure, so the outbox retries."""

    def __init__(self) -> None:
        if not settings.GITLAB_URL or not settings.GITLAB_TOKEN:
            raise ImproperlyConfigured("GITLAB_URL and GITLAB_TOKEN must be set")
        if not settings.GITLAB_URL.startswith("https://") and not settings.DEBUG:
            raise ImproperlyConfigured("GITLAB_URL must start with https://")
        self._client = httpx.Client(
            base_url=settings.GITLAB_URL.rstrip("/") + "/api/v4",
            headers={"PRIVATE-TOKEN": settings.GITLAB_TOKEN},
            timeout=settings.RMO_GITLAB_TIMEOUT_SECONDS,
        )

    def get_changes(self, project_id: int, iid: int) -> Changes:
        """Return every changed path (both paths of a rename) and the changed line count."""
        files: list[str] = []
        changed_lines = 0
        too_large = False
        deadline = _deadline()
        path = f"/projects/{project_id}/merge_requests/{iid}/diffs"
        page: int | None = 1
        while page is not None:
            response = self._request(
                "GET", path, deadline, params={"per_page": PER_PAGE, "page": page}
            )
            for diff in response.json():
                files.append(diff["new_path"])
                if diff["renamed_file"]:
                    files.append(diff["old_path"])
                changed_lines += sum(
                    line.startswith(("+", "-")) for line in diff["diff"].split("\n")
                )
                too_large = too_large or diff["too_large"] or diff["collapsed"]
            page = _next_page(response, page, path)
        return Changes(files=files, changed_lines=changed_lines, too_large=too_large)

    def set_reviewer(
        self, project_id: int, iid: int, reviewer_gitlab_user_id: int
    ) -> None:
        """Add a reviewer and keep the current ones. Send nothing if already a reviewer."""
        deadline = _deadline()
        path = f"/projects/{project_id}/merge_requests/{iid}"
        current = [
            r["id"] for r in self._request("GET", path, deadline).json()["reviewers"]
        ]
        if reviewer_gitlab_user_id in current:
            return
        self._request(
            "PUT",
            path,
            deadline,
            json={"reviewer_ids": [*current, reviewer_gitlab_user_id]},
        )

    def get_reviewer_state(self, project_id: int, iid: int, user_id: int) -> str | None:
        """Return the review state of one user on the MR, or None if not a reviewer."""
        response = self._request(
            "GET",
            f"/projects/{project_id}/merge_requests/{iid}/reviewers",
            _deadline(),
        )
        for reviewer in response.json():
            if reviewer["user"]["id"] == user_id:
                return reviewer["state"]
        return None

    def get_latest_verdict_time(
        self, project_id: int, iid: int, user_id: int
    ) -> datetime | None:
        """Return the time of the user's latest approval or change request, or None.

        GitLab logs each of them as a system note authored by the reviewer.
        """
        return self._latest_note_time(
            project_id,
            iid,
            lambda note: note["system"]
            and note["author"]["id"] == user_id
            and note["body"].strip().lower().startswith(VERDICT_TEXTS),
        )

    def get_latest_comment_time(
        self, project_id: int, iid: int, user_id: int
    ) -> datetime | None:
        """Return the time of the user's latest plain (non-system) note, or None."""
        return self._latest_note_time(
            project_id,
            iid,
            lambda note: not note["system"] and note["author"]["id"] == user_id,
        )

    def _latest_note_time(
        self, project_id: int, iid: int, matches: Callable[[dict], bool]
    ) -> datetime | None:
        """Page through the MR notes, newest first, and return the first match's time."""
        deadline = _deadline()
        path = f"/projects/{project_id}/merge_requests/{iid}/notes"
        page: int | None = 1
        while page is not None:
            response = self._request(
                "GET",
                path,
                deadline,
                params={
                    "sort": "desc",
                    "order_by": "created_at",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            for note in response.json():
                if matches(note):
                    return datetime.fromisoformat(note["created_at"])
            page = _next_page(response, page, path)
        return None

    def list_open_merge_requests(
        self, project_id: int, updated_after: str | None, page: int
    ) -> MergeRequestPage:
        """Return one page of open MRs, oldest update first."""
        params: dict[str, Any] = {
            "state": "opened",
            "order_by": "updated_at",
            "sort": "asc",
            "per_page": PER_PAGE,
            "page": page,
        }
        if updated_after is not None:
            params["updated_after"] = updated_after
        path = f"/projects/{project_id}/merge_requests"
        response = self._request("GET", path, _deadline(), params=params)
        return MergeRequestPage(
            merge_requests=[
                MergeRequestSummary(
                    iid=item["iid"],
                    updated_at=item["updated_at"],
                    author_id=item["author"]["id"],
                    title=item["title"],
                )
                for item in response.json()
            ],
            next_page=_next_page(response, page, path),
        )

    def post_note(self, project_id: int, iid: int, body: str) -> None:
        """Not implemented: no caller yet."""
        raise NotImplementedError("post_note has no caller yet")

    def _request(
        self, method: str, path: str, deadline: float, **kwargs: Any
    ) -> httpx.Response:
        """Send one request. Raise GitLabError without chaining, so no request data leaks.

        `deadline` is a monotonic time. A request that would start after it fails.
        """
        time_left = deadline - time.monotonic()
        if time_left <= 0:
            raise GitLabError(f"{method} {path} -> deadline")
        timeout = min(settings.RMO_GITLAB_TIMEOUT_SECONDS, time_left)
        try:
            response = self._client.request(method, path, timeout=timeout, **kwargs)
        except httpx.TimeoutException:
            raise GitLabError(f"{method} {path} -> timeout") from None
        except httpx.TransportError:
            raise GitLabError(f"{method} {path} -> transport error") from None
        if not response.is_success:
            raise GitLabError(f"{method} {path} -> {response.status_code}")
        return response


def _deadline() -> float:
    """Return the monotonic time at which a whole gateway call must be over."""
    return time.monotonic() + settings.RMO_GITLAB_CALL_DEADLINE_SECONDS


def _next_page(response: httpx.Response, current: int, path: str) -> int | None:
    """Return the next page number from X-Next-Page, or None on the last page.

    Raise when it does not move forward, so a bad header cannot loop forever.
    """
    value = response.headers.get("X-Next-Page", "")
    if not value:
        return None
    if int(value) <= current:
        raise GitLabError(f"GET {path} -> bad next page")
    return int(value)
