from datetime import UTC, datetime

from django.conf import settings
from django.utils import timezone

from audit.services import record
from integrations import outbox
from integrations.models import InboxEvent
from reviews.models import MergeRequest, MergeRequestState, Person

MERGE_REQUEST_EVENT = "Merge Request Hook"
NOTE_EVENT = "Note Hook"
AI_GATE_OPEN_STATES = (
    MergeRequestState.AWAITING_AI_REVIEW,
    MergeRequestState.WAITING_ON_AUTHOR,
)


def parse_gitlab_time(value: str) -> datetime:
    """Parse a GitLab timestamp into an aware datetime.

    GitLab sends ISO 8601 ("2026-11-02T09:45:57.123Z") and, in some webhooks,
    "2026-11-02 09:45:57 UTC". A time with no zone is read as UTC.
    """
    parsed = datetime.fromisoformat(value.removesuffix(" UTC").replace(" +0000", "Z"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _event_time(event: InboxEvent, *keys: str) -> datetime:
    """Return the first of the payload's `keys` timestamps, else when the event arrived.

    The AI gate compares this time with GitLab's note times, so GitLab's own
    clock is used when the payload has it.
    """
    attributes = event.payload["object_attributes"]
    for key in keys:
        if attributes.get(key):
            parsed = parse_gitlab_time(attributes[key])
            # A whole-second time may fall anywhere in that second: use its end,
            # so a bot note of the same second counts as older.
            return (
                parsed.replace(microsecond=999999) if not parsed.microsecond else parsed
            )
    return event.received_at or timezone.now()


def handle_event(event: InboxEvent) -> None:
    """Apply one inbox event. The caller owns the transaction (INV-4)."""
    payload = event.payload
    if event.event_kind == NOTE_EVENT:
        if payload["object_attributes"]["noteable_type"] == "MergeRequest":
            _queue_ai_review_check(
                payload["project"]["id"],
                payload["merge_request"]["iid"],
                _is_ai_reviewer(payload),
            )
    elif event.event_kind == MERGE_REQUEST_EVENT:
        attributes = payload["object_attributes"]
        project_id = payload["project"]["id"]
        if attributes.get("action") == "open":
            _open_merge_request(
                project_id, attributes, _event_time(event, "created_at", "updated_at")
            )
        else:
            if attributes.get("action") == "update" and attributes.get("oldrev"):
                _reopen_ai_gate(
                    project_id, attributes["iid"], _event_time(event, "updated_at")
                )
            else:
                _queue_ai_review_check(
                    project_id, attributes["iid"], _is_ai_reviewer(payload)
                )


def _is_ai_reviewer(payload: dict) -> bool:
    user_id = settings.RMO_AI_REVIEWER_GITLAB_USER_ID
    return user_id is not None and payload.get("user", {}).get("id") == user_id


def _queue_ai_review_check(project_id: int, iid: int, by_ai_reviewer: bool) -> None:
    """Queue a check for an MR awaiting AI review, or one the AI Reviewer touched.

    A bot hook is also queued for an MR waiting on its author, so a late
    approval is not lost.
    """
    merge_request = MergeRequest.objects.filter(project_id=project_id, iid=iid).first()
    if merge_request is not None and (
        merge_request.state == MergeRequestState.AWAITING_AI_REVIEW
        or (
            by_ai_reviewer
            and merge_request.state == MergeRequestState.WAITING_ON_AUTHOR
        )
    ):
        outbox.enqueue("check_ai_review", {"merge_request_id": merge_request.pk})


def _reopen_ai_gate(project_id: int, iid: int, entered_at: datetime) -> None:
    """Handle new commits: restart the AI gate clock and queue a check.

    The caller's transaction holds the row lock taken here, so a concurrent AI
    check cannot be overwritten. An MR waiting on its author returns to the AI
    gate. The entry time never moves back. A verdict older than it is ignored
    by the check.
    """
    merge_request = (
        MergeRequest.objects.select_for_update()
        .filter(project_id=project_id, iid=iid)
        .first()
    )
    if merge_request is None or merge_request.state not in AI_GATE_OPEN_STATES:
        return
    before = merge_request.state
    merge_request.ai_gate_entered_at = max(merge_request.ai_gate_entered_at, entered_at)
    merge_request.state = MergeRequestState.AWAITING_AI_REVIEW
    merge_request.save(update_fields=["state", "ai_gate_entered_at"])
    if before != merge_request.state:
        record(
            "gitlab",
            "mr.new_commits",
            merge_request,
            {"state": before},
            {"state": merge_request.state},
        )
    outbox.enqueue("check_ai_review", {"merge_request_id": merge_request.pk})


def _open_merge_request(
    project_id: int, attributes: dict, entered_at: datetime
) -> None:
    if MergeRequest.objects.filter(
        project_id=project_id, iid=attributes["iid"]
    ).exists():
        return
    author = Person.objects.get(gitlab_user_id=attributes["author_id"])
    merge_request = MergeRequest.objects.create(
        project_id=project_id,
        iid=attributes["iid"],
        author=author,
        title=attributes.get("title", ""),
        state=MergeRequestState.AWAITING_AI_REVIEW,
        ai_gate_entered_at=entered_at,
    )
    record(
        "gitlab",
        "mr.opened",
        merge_request,
        None,
        {"state": merge_request.state},
    )
