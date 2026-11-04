from datetime import datetime

from integrations.adapters import Changes, MergeRequestPage, MergeRequestStatus


class FakeGitLabGateway:  # pylint: disable=too-many-instance-attributes
    """In-memory gateway. It records calls and returns seeded changes."""

    def __init__(self) -> None:
        self.changes: dict[tuple[int, int], Changes] = {}
        self.reviewer_states: dict[tuple[int, int, int], str] = {}
        self.latest_verdict_times: dict[tuple[int, int, int], datetime] = {}
        self.latest_comment_times: dict[tuple[int, int, int], datetime] = {}
        self.version_times: dict[tuple[int, int, str], datetime] = {}
        self.open_merge_requests: dict[int, MergeRequestPage] = {}
        self.merge_request_statuses: dict[tuple[int, int], MergeRequestStatus] = {}
        self.calls: list[tuple] = []

    def get_changes(self, project_id: int, iid: int) -> Changes:
        return self.changes.get((project_id, iid), Changes(files=[], changed_lines=0))

    def set_reviewer(
        self, project_id: int, iid: int, reviewer_gitlab_user_id: int
    ) -> None:
        self.calls.append(("set_reviewer", project_id, iid, reviewer_gitlab_user_id))

    def get_reviewer_state(self, project_id: int, iid: int, user_id: int) -> str | None:
        return self.reviewer_states.get((project_id, iid, user_id))

    def get_latest_verdict_time(
        self, project_id: int, iid: int, user_id: int
    ) -> datetime | None:
        return self.latest_verdict_times.get((project_id, iid, user_id))

    def get_latest_comment_time(
        self, project_id: int, iid: int, user_id: int
    ) -> datetime | None:
        return self.latest_comment_times.get((project_id, iid, user_id))

    def get_version_time(
        self, project_id: int, iid: int, head_sha: str
    ) -> datetime | None:
        return self.version_times.get((project_id, iid, head_sha))

    def list_open_merge_requests(  # pylint: disable=unused-argument
        self, project_id: int, updated_after: str | None, page: int
    ) -> MergeRequestPage:
        return self.open_merge_requests.get(project_id, MergeRequestPage([], None))

    def get_merge_request(self, project_id: int, iid: int) -> MergeRequestStatus:
        return self.merge_request_statuses.get(
            (project_id, iid), MergeRequestStatus("opened", None, None)
        )

    def post_note(self, project_id: int, iid: int, body: str) -> None:
        self.calls.append(("post_note", project_id, iid, body))


class FakeNotifier:  # pylint: disable=too-few-public-methods
    """In-memory notifier. It records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[int], str]] = []

    def notify(self, gitlab_user_ids: list[int], text: str) -> None:
        self.calls.append((gitlab_user_ids, text))
