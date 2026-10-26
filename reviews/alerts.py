from integrations import outbox
from reviews.models import Person


def queue_notification(gitlab_user_ids: list[int], text: str) -> None:
    """Queue a notify call. The caller owns the transaction."""
    outbox.enqueue("notify", {"gitlab_user_ids": gitlab_user_ids, "text": text})


def queue_admin_alert(text: str) -> None:
    """Queue a notify call addressed to every admin Person."""
    admin_ids = list(
        Person.objects.filter(is_admin=True)
        .order_by("id")
        .values_list("gitlab_user_id", flat=True)
    )
    queue_notification(admin_ids, text)
