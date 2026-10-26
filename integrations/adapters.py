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


class GitLabGatewayInterface(Protocol):
    """GitLab calls. An implementation raises on failure, so the outbox retries."""

    def get_changes(self, project_id: int, iid: int) -> Changes: ...

    def set_reviewer(
        self, project_id: int, iid: int, reviewer_gitlab_user_id: int
    ) -> None: ...

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
