from audit.services import record
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
