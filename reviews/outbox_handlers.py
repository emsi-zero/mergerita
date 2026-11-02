import zlib

from django.db import connection

from integrations.adapters import get_gitlab_gateway, get_notifier

SET_REVIEWER_LOCK_NAMESPACE = 7301


class ReviewerLockBusy(Exception):
    """Another worker is setting a reviewer on this MR. The outbox retries later."""


def set_reviewer(payload: dict) -> None:
    """Outbox handler: set the reviewer in GitLab. Raises on failure.

    The gateway reads the reviewers and then writes them. Two workers doing this
    for one MR would lose an update, so hold a per-MR advisory lock around the
    call. When another worker holds it, raise ReviewerLockBusy at once, so the
    outbox backs off and the lease is not spent waiting. The lock is
    session-level: no transaction is open while it is held. Session-level
    advisory locks need a direct Postgres connection, and do not work behind
    PgBouncer in transaction-pooling mode.
    """
    project_id, iid = payload["project_id"], payload["iid"]
    key = zlib.crc32(f"{project_id}:{iid}".encode()) & 0x7FFFFFFF
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s, %s)", [SET_REVIEWER_LOCK_NAMESPACE, key]
        )
        if not cursor.fetchone()[0]:
            raise ReviewerLockBusy(f"MR {project_id}:{iid} is being updated")
        try:
            get_gitlab_gateway().set_reviewer(
                project_id, iid, payload["reviewer_gitlab_user_id"]
            )
        finally:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)", [SET_REVIEWER_LOCK_NAMESPACE, key]
            )


def notify(payload: dict) -> None:
    """Outbox handler: send a notification. Raises on failure."""
    get_notifier().notify(payload["gitlab_user_ids"], payload["text"])
