import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import connection, transaction

from integrations import outbox
from integrations.adapters import (
    GitLabGatewayInterface,
    MergeRequestSummary,
    get_gitlab_gateway,
)
from integrations.gitlab import PER_PAGE, GitLabError
from integrations.inbox import store_event
from integrations.models import OutboxCall, OutboxStatus
from reviews.lifecycle import MERGE_REQUEST_EVENT, RECONCILE_SOURCE, parse_gitlab_time
from reviews.models import MergeRequest, MergeRequestState, ReconcileCursor

logger = logging.getLogger(__name__)

RECONCILE_LOCK_KEY = 7302


def reconcile_all() -> None:
    """Turn the open MRs of every configured project into inbox events.

    Hold one session-level advisory lock for the whole run, so a second worker
    skips it (GLI-10). No transaction is open while the lock is held across
    HTTP calls. Session-level advisory locks need a direct Postgres connection,
    and do not work behind PgBouncer in transaction-pooling mode.

    Any error in one project is logged and leaves that project's cursor where
    its last committed page put it; the run goes on with the next project, and
    the worker never crashes on a reconciliation error.
    """
    if not settings.RMO_GITLAB_PROJECT_IDS:
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [RECONCILE_LOCK_KEY])
        if not cursor.fetchone()[0]:
            logger.info("reconcile skipped: another worker is running it")
            return
        try:
            for project_id in settings.RMO_GITLAB_PROJECT_IDS:
                for step in (_reconcile_project, _reconcile_merges):
                    try:
                        step(project_id)
                    except Exception:  # pylint: disable=broad-exception-caught
                        logger.exception(
                            "reconcile %s of project %s failed",
                            step.__name__,
                            project_id,
                        )
        finally:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [RECONCILE_LOCK_KEY])


def _reconcile_project(project_id: int) -> None:
    """Read the project's open MRs, oldest update first, until nothing new is left.

    Paging is by cursor, not by offset: an offset moves when an MR is updated
    between two calls, and the MR behind it would be skipped. Every call asks
    for page 1 of the MRs updated since the cursor (the first call: minus the
    lag). The lag makes the first call list a few MRs again; the keys already stored in this run are
    dropped. The run ends when a call brings no new (iid, updated_at) key.
    More than a page of MRs with one exact updated_at is a window this scheme
    cannot cross, so it raises and the cursor stays.
    """
    gateway = get_gitlab_gateway()
    stored = ReconcileCursor.objects.filter(project_id=project_id).first()
    cursor_time = stored.updated_after if stored else None
    lag = timedelta(seconds=settings.RMO_RECONCILE_LAG_SECONDS)
    seen: set[tuple[int, str]] = set()
    first_call = True
    while True:
        # Only the first call looks back by the lag. Later calls start at the cursor
        # (inclusive), so a window of many MRs cannot hold the run back.
        since = cursor_time - lag if cursor_time and first_call else cursor_time
        updated_after = since.isoformat() if since else None
        first_call = False
        result = gateway.list_open_merge_requests(project_id, updated_after, 1)
        page = result.merge_requests
        new = [mr for mr in page if (mr.iid, mr.updated_at) not in seen]
        if not new:
            if result.next_page is not None:
                raise GitLabError(
                    f"project {project_id}: more than {PER_PAGE} MRs "
                    "share one updated_at"
                )
            return
        # GitLab is read here, outside the inbox transaction (INV-1).
        payloads = _payloads(project_id, new, gateway)
        last_time = parse_gitlab_time(page[-1].updated_at)
        cursor_time = max(cursor_time, last_time) if cursor_time else last_time
        _store_page(project_id, new, payloads, cursor_time)
        seen.update((mr.iid, mr.updated_at) for mr in new)
        if result.next_page is None:
            return


def _reconcile_merges(project_id: int) -> None:
    """Store a `merged` event for each MR held as approved that GitLab shows merged (MRG-9).

    GitLab is read here, outside the inbox transaction (INV-4). An MR whose
    read fails, for any reason, is logged and skipped, so one bad MR does not hide the others.
    """
    gateway = get_gitlab_gateway()
    merged = []
    for iid in MergeRequest.objects.filter(
        project_id=project_id, state=MergeRequestState.APPROVED
    ).values_list("iid", flat=True):
        try:
            status = gateway.get_merge_request(project_id, iid)
        except Exception as error:  # pylint: disable=broad-exception-caught
            # Log the MR and the error type only: nothing from the request.
            logger.error(
                "merge check of MR %s:%s failed: %s",
                project_id,
                iid,
                type(error).__name__,
            )
            continue
        if status.state == "merged":
            merged.append((iid, status))
    with transaction.atomic():
        for iid, status in merged:
            merged_at = status.merged_at.isoformat() if status.merged_at else None
            stored = store_event(
                RECONCILE_SOURCE,
                f"{project_id}:{iid}:merged:{merged_at}",
                MERGE_REQUEST_EVENT,
                f"{project_id}:{iid}",
                {
                    "project": {"id": project_id},
                    "object_attributes": {
                        "iid": iid,
                        "action": "merged",
                        "merged_at": merged_at,
                    },
                },
            )
            if not stored:
                _requeue_confirmation(project_id, iid)


def _requeue_confirmation(project_id: int, iid: int) -> None:
    """Queue `confirm_merge` again when the stored event's job is gone and the MR is approved.

    The inbox key of a merge is stable, so a repeat reconciliation stores
    nothing. A job that ended FAILED would never run again without this.
    """
    merge_request = MergeRequest.objects.filter(
        project_id=project_id, iid=iid, state=MergeRequestState.APPROVED
    ).first()
    if merge_request is None:
        return
    payload = {"merge_request_id": merge_request.pk}
    if not OutboxCall.objects.filter(
        kind="confirm_merge",
        payload=payload,
        status__in=(OutboxStatus.PENDING, OutboxStatus.IN_FLIGHT),
    ).exists():
        outbox.enqueue("confirm_merge", payload)


def _payloads(
    project_id: int,
    summaries: list[MergeRequestSummary],
    gateway: GitLabGatewayInterface,
) -> list[dict]:
    """Build one inbox payload per MR; read the push time only where the sha is new."""
    known_shas = dict(
        MergeRequest.objects.filter(
            project_id=project_id, iid__in=[mr.iid for mr in summaries]
        ).values_list("iid", "head_sha")
    )
    payloads = []
    for summary in summaries:
        attributes = {
            "iid": summary.iid,
            "action": "reconcile",
            "author_id": summary.author_id,
            "title": summary.title,
            "sha": summary.sha,
            "updated_at": summary.updated_at,
            "web_url": summary.web_url,
        }
        changed = (
            summary.iid not in known_shas or known_shas[summary.iid] != summary.sha
        )
        if changed and summary.sha:
            version_time = gateway.get_version_time(
                project_id, summary.iid, summary.sha
            )
            if version_time is not None:
                attributes["version_created_at"] = version_time.isoformat()
        payloads.append(
            {"project": {"id": project_id}, "object_attributes": attributes}
        )
    return payloads


def _store_page(
    project_id: int,
    summaries: list[MergeRequestSummary],
    payloads: list[dict],
    cursor_time: datetime,
) -> None:
    """Store the page's events and advance the cursor in one transaction (INV-2)."""
    with transaction.atomic():
        for summary, payload in zip(summaries, payloads):
            store_event(
                RECONCILE_SOURCE,
                f"{project_id}:{summary.iid}:{summary.updated_at}",
                MERGE_REQUEST_EVENT,
                f"{project_id}:{summary.iid}",
                payload,
            )
        ReconcileCursor.objects.update_or_create(
            project_id=project_id, defaults={"updated_after": cursor_time}
        )
