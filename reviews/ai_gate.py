from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Max

from audit.services import record
from integrations import outbox
from integrations.adapters import get_gitlab_gateway
from reviews.alerts import queue_notification
from reviews.lifecycle import AI_GATE_OPEN_STATES
from reviews.models import MergeRequest, MergeRequestState

# Review states of the AI Reviewer user. Any other state leaves the MR unchanged.
APPROVED_STATE = "approved"
REQUESTED_CHANGES_STATE = "requested_changes"
PASS_STATES = frozenset({APPROVED_STATE})
FAIL_STATES = frozenset({REQUESTED_CHANGES_STATE, "reviewed"})


def check_ai_review(payload: dict) -> None:
    """Outbox handler: move an MR by the AI Reviewer's fresh verdict.

    First read the times of the AI Reviewer's latest verdict system note and
    latest plain comment, then its review state, all with no transaction open
    (INV-1). The bot comments before it changes its state, so the times are read
    first: a state that is newer than the event is still safe, an older one is
    ignored as not fresh. `approved` and `requested_changes` use the verdict
    note, `reviewed` uses the comment. Then lock the MR and act only when that
    event is newer than the time the MR entered the AI gate (and, for an MR
    waiting on its author, than every open change request of a human reviewer).
    An MR awaiting the AI review follows the state; an MR waiting on its author moves only on
    `approved` (the bot approved late).
    """
    user_id = settings.RMO_AI_REVIEWER_GITLAB_USER_ID
    if user_id is None:
        raise ImproperlyConfigured("RMO_AI_REVIEWER_GITLAB_USER_ID is not set")
    merge_request_id = payload["merge_request_id"]
    target = MergeRequest.objects.get(pk=merge_request_id)
    gateway = get_gitlab_gateway()
    verdict_time = gateway.get_latest_verdict_time(
        target.project_id, target.iid, user_id
    )
    comment_time = gateway.get_latest_comment_time(
        target.project_id, target.iid, user_id
    )
    reviewer_state = gateway.get_reviewer_state(target.project_id, target.iid, user_id)
    # A comment-only review ("reviewed") writes no system note: its comment is the event.
    event_time = comment_time if reviewer_state == "reviewed" else verdict_time
    if event_time is None or reviewer_state not in PASS_STATES | FAIL_STATES:
        return
    with transaction.atomic():
        merge_request = (
            MergeRequest.objects.select_related("author")
            .select_for_update(of=("self",))
            .get(pk=merge_request_id)
        )
        before = merge_request.state
        threshold = merge_request.ai_gate_entered_at
        if before == MergeRequestState.WAITING_ON_AUTHOR:
            # A human reviewer's open change request holds the MR until a push.
            open_request = merge_request.area_reviews.aggregate(
                latest=Max("changes_requested_at")
            )["latest"]
            threshold = max(threshold, open_request or threshold)
        if event_time <= threshold:
            return
        if reviewer_state in PASS_STATES and before in AI_GATE_OPEN_STATES:
            new_state = MergeRequestState.AWAITING_BOOKING
        elif (
            reviewer_state in FAIL_STATES
            and before == MergeRequestState.AWAITING_AI_REVIEW
        ):
            new_state = MergeRequestState.WAITING_ON_AUTHOR
        else:
            return
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
        else:
            queue_notification(
                [merge_request.author.gitlab_user_id],
                f"The AI Reviewer asked for changes on MR "
                f"{merge_request.project_id}:{merge_request.iid}.",
            )
