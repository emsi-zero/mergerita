from integrations.adapters import get_gitlab_gateway, get_notifier


def set_reviewer(payload: dict) -> None:
    """Outbox handler: set the reviewer in GitLab. Raises on failure."""
    get_gitlab_gateway().set_reviewer(
        payload["project_id"], payload["iid"], payload["reviewer_gitlab_user_id"]
    )


def notify(payload: dict) -> None:
    """Outbox handler: send a notification. Raises on failure."""
    get_notifier().notify(payload["gitlab_user_ids"], payload["text"])
