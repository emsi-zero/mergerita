import functools
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class Changes:
    """The files and the number of changed lines of one merge request."""

    files: list[str]
    changed_lines: int
    too_large: bool = False


@dataclass(frozen=True)
class MergeRequestSummary:
    """The fields of one open merge request that reconciliation reads."""

    iid: int
    updated_at: str
    author_id: int
    title: str


@dataclass(frozen=True)
class MergeRequestPage:
    """One page of open merge requests. `next_page` is None on the last page."""

    merge_requests: list[MergeRequestSummary]
    next_page: int | None


class GitLabGatewayInterface(Protocol):
    """GitLab calls. An implementation raises on failure, so the outbox retries."""

    def get_changes(self, project_id: int, iid: int) -> Changes: ...

    def set_reviewer(
        self, project_id: int, iid: int, reviewer_gitlab_user_id: int
    ) -> None: ...

    def get_reviewer_state(
        self, project_id: int, iid: int, user_id: int
    ) -> str | None: ...

    def list_open_merge_requests(
        self, project_id: int, updated_after: str | None, page: int
    ) -> MergeRequestPage: ...

    def post_note(self, project_id: int, iid: int, body: str) -> None: ...


class NotifierInterface(Protocol):  # pylint: disable=too-few-public-methods
    """Chat notifications. An implementation raises on failure, so the outbox retries."""

    def notify(self, gitlab_user_ids: list[int], text: str) -> None: ...


@functools.cache
def get_gitlab_gateway() -> GitLabGatewayInterface:
    """Return the gateway named in settings. One instance per process."""
    return import_string(settings.RMO_GITLAB_GATEWAY)()


@functools.cache
def get_notifier() -> NotifierInterface:
    """Return the notifier named in settings. One instance per process."""
    return import_string(settings.RMO_NOTIFIER)()
