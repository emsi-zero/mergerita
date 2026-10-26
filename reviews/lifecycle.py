from django.conf import settings

from audit.services import record
from integrations import outbox
from integrations.models import InboxEvent
from reviews.models import MergeRequest, MergeRequestState, Person

MERGE_REQUEST_EVENT = "Merge Request Hook"


def handle_event(event: InboxEvent) -> None:
    """Apply one inbox event. The caller owns the transaction (INV-4)."""
    if event.event_kind != MERGE_REQUEST_EVENT:
        return
    attributes = event.payload["object_attributes"]
    if attributes.get("action") == "open":
        _open_merge_request(event.payload["project"]["id"], attributes)
    elif attributes.get("action") == "update":
        _apply_labels(event.payload["project"]["id"], attributes["iid"], event.payload)


def _apply_labels(project_id: int, iid: int, payload: dict) -> None:
    """Apply the AI-review verdict label. The fail label wins over the pass label."""
    merge_request = MergeRequest.objects.filter(project_id=project_id, iid=iid).first()
    if (
        merge_request is None
        or merge_request.state != MergeRequestState.AWAITING_AI_REVIEW
    ):
        return
    labels = {label["title"] for label in payload.get("labels", [])}
    if settings.RMO_AI_FAIL_LABEL in labels:
        new_state = MergeRequestState.WAITING_ON_AUTHOR
    elif settings.RMO_AI_PASS_LABEL in labels:
        new_state = MergeRequestState.AWAITING_BOOKING
    else:
        return
    before = merge_request.state
    merge_request.state = new_state
    merge_request.save(update_fields=["state"])
    record(
        "gitlab",
        "mr.ai_review_verdict",
        merge_request,
        {"state": before},
        {"state": new_state},
    )
    if new_state == MergeRequestState.AWAITING_BOOKING:
        outbox.enqueue("resolve_areas", {"merge_request_id": merge_request.pk})


def _open_merge_request(project_id: int, attributes: dict) -> None:
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
    )
    record(
        "gitlab",
        "mr.opened",
        merge_request,
        None,
        {"state": merge_request.state},
    )
