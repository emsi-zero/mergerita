"""Requests from a reviewer that the MR author joins the review, with audit."""

from django.db import transaction
from django.utils import timezone

from audit.services import record
from reviews.alerts import queue_notification
from reviews.models import (
    AreaReview,
    AreaReviewState,
    Person,
    PresenceRequest,
    PresenceRequestState,
)


def _lock_booked_review(reviewer: Person, area_review_id: int) -> AreaReview:
    """Lock and return the area review, when it is booked to `reviewer` (INV-3)."""
    return (
        AreaReview.objects.select_for_update(of=("self",))
        .select_related("merge_request__author")
        .get(
            pk=area_review_id,
            reviewer=reviewer,
            state=AreaReviewState.BOOKED,
        )
    )


def request_presence(
    reviewer: Person, area_review_id: int, note: str, actor: str
) -> bool:
    """Ask the MR author to join the review, with one audit event and one chat message.

    Return False and change nothing when a request is already active.
    Raise `AreaReview.DoesNotExist` when the review is not booked to `reviewer`.
    """
    with transaction.atomic():
        review = _lock_booked_review(reviewer, area_review_id)
        if review.presence_requests.filter(state=PresenceRequestState.ACTIVE).exists():
            return False
        presence = PresenceRequest.objects.create(
            area_review=review, requested_by=reviewer, note=note
        )
        day = review.booked_day.isoformat()
        merge_request = review.merge_request
        record(
            actor,
            "presence.requested",
            presence,
            None,
            {"area_review_id": review.pk, "day": day, "note": note},
        )
        text = (
            f"{reviewer.name} asks you to join the review of "
            f"!{merge_request.iid} {merge_request.title} on {day}."
        )
        queue_notification(
            [merge_request.author.gitlab_user_id], f"{text} {note}" if note else text
        )
        return True


def withdraw_active_presence(review: AreaReview, reviewer: Person, actor: str) -> bool:
    """Withdraw the active request of a locked area review, with audit and chat.

    Return False when no request is active. The caller owns the transaction.
    `reviewer` is named in the chat message to the author.
    """
    presence = review.presence_requests.filter(
        state=PresenceRequestState.ACTIVE
    ).first()
    if presence is None:
        return False
    presence.state = PresenceRequestState.WITHDRAWN
    presence.withdrawn_at = timezone.now()
    presence.save(update_fields=["state", "withdrawn_at"])
    merge_request = review.merge_request
    record(
        actor,
        "presence.withdrawn",
        presence,
        {"state": PresenceRequestState.ACTIVE},
        {"state": PresenceRequestState.WITHDRAWN},
    )
    queue_notification(
        [merge_request.author.gitlab_user_id],
        f"{reviewer.name} no longer needs you at the review of "
        f"!{merge_request.iid} on {review.booked_day.isoformat()}.",
    )
    return True


def withdraw_presence(reviewer: Person, area_review_id: int, actor: str) -> bool:
    """Withdraw the active request, with one audit event and one chat message.

    Return False and change nothing when no request is active.
    Raise `AreaReview.DoesNotExist` when the review is not booked to `reviewer`.
    """
    with transaction.atomic():
        review = _lock_booked_review(reviewer, area_review_id)
        return withdraw_active_presence(review, reviewer, actor)
