from integrations.adapters import Changes, MergeRequestPage


class FakeGitLabGateway:
    """In-memory gateway. It records calls and returns seeded changes."""

    def __init__(self) -> None:
        self.changes: dict[tuple[int, int], Changes] = {}
        self.reviewer_states: dict[tuple[int, int, int], str] = {}
        self.open_merge_requests: dict[int, MergeRequestPage] = {}
        self.calls: list[tuple] = []

    def get_changes(self, project_id: int, iid: int) -> Changes:
        return self.changes.get((project_id, iid), Changes(files=[], changed_lines=0))

    def set_reviewer(
        self, project_id: int, iid: int, reviewer_gitlab_user_id: int
    ) -> None:
        self.calls.append(("set_reviewer", project_id, iid, reviewer_gitlab_user_id))

    def get_reviewer_state(self, project_id: int, iid: int, user_id: int) -> str | None:
        return self.reviewer_states.get((project_id, iid, user_id))

    def list_open_merge_requests(  # pylint: disable=unused-argument
        self, project_id: int, updated_after: str | None, page: int
    ) -> MergeRequestPage:
        return self.open_merge_requests.get(project_id, MergeRequestPage([], None))

    def post_note(self, project_id: int, iid: int, body: str) -> None:
        self.calls.append(("post_note", project_id, iid, body))


class FakeNotifier:  # pylint: disable=too-few-public-methods
    """In-memory notifier. It records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[int], str]] = []

    def notify(self, gitlab_user_ids: list[int], text: str) -> None:
        self.calls.append((gitlab_user_ids, text))
