from datetime import UTC, datetime
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from audit.services import record
from integrations import outbox
from integrations.models import InboxEvent
from reviews.models import MergeRequest, MergeRequestState, Person

MERGE_REQUEST_EVENT = "Merge Request Hook"
NOTE_EVENT = "Note Hook"
RECONCILE_SOURCE = "reconcile"
COVERAGE_STATES = (
    MergeRequestState.IN_REVIEW,
    MergeRequestState.WAITING_ON_AUTHOR,
    MergeRequestState.APPROVED,
)
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
            _queue_coverage_check(
                payload["project"]["id"], payload["merge_request"]["iid"]
            )
            _queue_ai_review_check(
                payload["project"]["id"],
                payload["merge_request"]["iid"],
                _is_ai_reviewer(payload),
            )
    elif event.event_kind == MERGE_REQUEST_EVENT:
        attributes = payload["object_attributes"]
        project_id = payload["project"]["id"]
        action = attributes.get("action")
        if action == "reconcile":
            # Only the reconciler may send this action; a webhook with it is ignored.
            if event.source == RECONCILE_SOURCE:
                _reconcile_merge_request(event, project_id, attributes)
                _queue_coverage_check(project_id, attributes["iid"])
        elif action == "merge":
            _queue_merge_confirmation(project_id, attributes["iid"])
        elif action == "merged":
            # Only the reconciler may send this action; a webhook with it is ignored.
            if event.source == RECONCILE_SOURCE:
                _queue_merge_confirmation(project_id, attributes["iid"])
        elif action == "open":
            _open_merge_request(
                project_id,
                attributes,
                _event_time(event, "created_at", "updated_at"),
                _hook_sha(payload),
            )
        elif action == "update" and attributes.get("oldrev"):
            pushed_at = _event_time(event, "updated_at")
            _record_head_sha(
                project_id, attributes["iid"], _hook_sha(payload), pushed_at
            )
            _reopen_ai_gate(project_id, attributes["iid"], pushed_at)
        else:
            _queue_ai_review_check(
                project_id, attributes["iid"], _is_ai_reviewer(payload)
            )
        if action != "reconcile":
            _queue_coverage_check(project_id, attributes["iid"])
            _record_web_url(project_id, attributes["iid"], attributes.get("url"))


def _queue_merge_confirmation(project_id: int, iid: int) -> None:
    """Queue the check of a reported merge in GitLab, unless the MR is already merged.

    The payload is not trusted: the job reads who merged, and when, from GitLab.
    """
    merge_request = (
        MergeRequest.objects.filter(project_id=project_id, iid=iid)
        .exclude(state=MergeRequestState.MERGED)
        .first()
    )
    if merge_request is not None:
        outbox.enqueue("confirm_merge", {"merge_request_id": merge_request.pk})


def _record_web_url(project_id: int, iid: int, value: object) -> None:
    """Store the MR's web address when it is an absolute http(s) URL (INV-4).

    Never raise: text that is too long, cannot be parsed, is not text, or is not
    an absolute http(s) URL is dropped, and the stored address stays.
    """
    if not isinstance(value, str):
        return
    if len(value) > MergeRequest._meta.get_field("web_url").max_length:
        return
    try:
        parts = urlsplit(value)
    except ValueError:
        return
    if parts.scheme in ("http", "https") and parts.netloc:
        MergeRequest.objects.filter(project_id=project_id, iid=iid).update(
            web_url=value
        )


def _hook_sha(payload: dict) -> str | None:
    """Return the head commit sha of a Merge Request Hook, or None when it has none."""
    commit = (
        payload["object_attributes"].get("last_commit")
        or payload.get("last_commit")
        or {}
    )
    return commit.get("id")


def _record_head_sha(
    project_id: int, iid: int, head_sha: str | None, pushed_at: datetime
) -> None:
    if head_sha:
        MergeRequest.objects.filter(project_id=project_id, iid=iid).filter(
            Q(head_sha_at__isnull=True) | Q(head_sha_at__lt=pushed_at)
        ).update(head_sha=head_sha, head_sha_at=pushed_at)


def _reconcile_merge_request(
    event: InboxEvent, project_id: int, attributes: dict
) -> None:
    """Handle an MR that reconciliation listed.

    An unknown MR is created as for an `open` hook, and a check is queued,
    because the AI Reviewer may have approved it already. A known MR whose head
    sha changed has new commits; the push time is the creation time of the diff
    version with the listed sha, which the reconciler read. A snapshot with no
    such version, or not newer than the stored push time, is stale: it changes
    nothing. A stored sha of None is recorded and is not a
    push. Any other known MR counts as an update, and a check is queued for it
    (also in `waiting_on_author`, where a lost bot approval is recovered).
    """
    head_sha = attributes.get("sha")
    entered_at = _event_time(event, "version_created_at", "updated_at")
    created = _open_merge_request(project_id, attributes, entered_at, head_sha)
    web_url = attributes.get("web_url")
    if created is not None:
        _record_web_url(project_id, attributes["iid"], web_url)
        outbox.enqueue("check_ai_review", {"merge_request_id": created.pk})
        return
    merge_request = MergeRequest.objects.select_for_update().get(
        project_id=project_id, iid=attributes["iid"]
    )
    previous_sha = merge_request.head_sha
    # Without the version of the listed sha the push cannot be dated: stale.
    is_newer = "version_created_at" in attributes and (
        merge_request.head_sha_at is None or entered_at > merge_request.head_sha_at
    )
    if not (head_sha and head_sha != previous_sha and not is_newer):
        # A stale snapshot must not overwrite a newer address either.
        _record_web_url(project_id, attributes["iid"], web_url)
    if head_sha and head_sha != previous_sha and is_newer:
        merge_request.head_sha = head_sha
        merge_request.head_sha_at = entered_at
        merge_request.save(update_fields=["head_sha", "head_sha_at"])
        if previous_sha:
            _reopen_ai_gate(project_id, attributes["iid"], entered_at)
            return
    _queue_ai_review_check(project_id, attributes["iid"], True)


def _is_ai_reviewer(payload: dict) -> bool:
    user_id = settings.RMO_AI_REVIEWER_GITLAB_USER_ID
    return user_id is not None and payload.get("user", {}).get("id") == user_id


def _queue_ai_review_check(project_id: int, iid: int, include_waiting: bool) -> None:
    """Queue a check for an MR awaiting AI review.

    An MR waiting on its author is queued too when `include_waiting` is set (a
    hook from the AI Reviewer, or a reconciliation), so a late approval is not
    lost.
    """
    merge_request = MergeRequest.objects.filter(project_id=project_id, iid=iid).first()
    if merge_request is not None and (
        merge_request.state == MergeRequestState.AWAITING_AI_REVIEW
        or (
            include_waiting
            and merge_request.state == MergeRequestState.WAITING_ON_AUTHOR
        )
    ):
        outbox.enqueue("check_ai_review", {"merge_request_id": merge_request.pk})


def _queue_coverage_check(project_id: int, iid: int) -> None:
    """Queue one coverage check for an MR in review, waiting on its author or approved.

    An approved MR is checked too, so a revoked approval is seen (MRG-11).
    """
    merge_request = MergeRequest.objects.filter(
        project_id=project_id,
        iid=iid,
        state__in=COVERAGE_STATES,
    ).first()
    if merge_request is not None:
        outbox.enqueue("check_coverage", {"merge_request_id": merge_request.pk})


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
    # A push closes the human change requests: the AI gate and the follow-up take over.
    merge_request.area_reviews.update(changes_requested_at=None)
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
    project_id: int, attributes: dict, entered_at: datetime, head_sha: str | None
) -> MergeRequest | None:
    """Create the MR. Return it, or None when it already exists."""
    if MergeRequest.objects.filter(
        project_id=project_id, iid=attributes["iid"]
    ).exists():
        return None
    author = Person.objects.get(gitlab_user_id=attributes["author_id"])
    merge_request = MergeRequest.objects.create(
        project_id=project_id,
        iid=attributes["iid"],
        author=author,
        title=attributes.get("title", ""),
        state=MergeRequestState.AWAITING_AI_REVIEW,
        ai_gate_entered_at=entered_at,
        head_sha=head_sha,
        head_sha_at=entered_at if head_sha else None,
    )
    record(
        "gitlab",
        "mr.opened",
        merge_request,
        None,
        {"state": merge_request.state},
    )
    return merge_request
