from integrations.models import InboxEvent


def store_event(
    source: str, event_uuid: str, event_kind: str, mr_key: str | None, payload: dict
) -> bool:
    """Store an inbox event. Return False when the event UUID is already stored."""
    _, created = InboxEvent.objects.get_or_create(
        source=source,
        event_uuid=event_uuid,
        defaults={"event_kind": event_kind, "mr_key": mr_key, "payload": payload},
    )
    return created
