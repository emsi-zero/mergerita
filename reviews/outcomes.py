"""Outcomes a reviewer reports on a pending review session, with audit."""

from django.db import transaction
from django.utils import timezone

from audit.services import record
from reviews import booking, presence
from reviews.alerts import queue_notification
from reviews.models import (
    AreaReviewState,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
)


def mark_done(reviewer: Person, session_id: int, actor: str) -> None:
    """Close the session as done. The area review's approval stays as it is (RVO-2).

    Raise `ReviewSession.DoesNotExist` when the session is not the pending
    session of a booked area review of `reviewer` (RVO-5).
    """
    with transaction.atomic():
        session = _lock_pending_session(reviewer, session_id)
        _close(session, ReviewSessionOutcome.DONE, actor)


def need_more_time(reviewer: Person, session_id: int, actor: str) -> bool:
    """Close the session and book the same reviewer on the next day that fits (RVO-3).

    Return False when no day fits and the area review became unbooked.
    Raise `ReviewSession.DoesNotExist` as `mark_done` does.
    """
    with transaction.atomic():
        booking.lock_booking()
        session = _lock_pending_session(reviewer, session_id)
        _close(session, ReviewSessionOutcome.MORE_TIME, actor)
        presence.withdraw_active_presence(session.area_review, reviewer, actor)
        area_review = session.area_review
        booked = booking.book_area_review(
            area_review,
            only_reviewer=reviewer,
            after_day=session.day,
            continuation=True,
        )
        if booked:
            queue_notification(
                [reviewer.gitlab_user_id],
                f"Next session for !{area_review.merge_request.iid} booked on "
                f"{area_review.booked_day.isoformat()}.",
            )
        return booked


def hand_back(reviewer: Person, session_id: int, actor: str) -> bool:
    """Close the session, give its minutes back, and book another owner (RVO-4).

    Return False when no other owner fits and the area review became unbooked.
    Raise `ReviewSession.DoesNotExist` as `mark_done` does.
    """
    with transaction.atomic():
        booking.lock_booking()
        session = _lock_pending_session(reviewer, session_id)
        _close(session, ReviewSessionOutcome.HANDED_BACK, actor)
        presence.withdraw_active_presence(session.area_review, reviewer, actor)
        booking.release_session_minutes(session)
        return booking.book_area_review(
            session.area_review, exclude={reviewer.pk}, continuation=True
        )


def _lock_pending_session(reviewer: Person, session_id: int) -> ReviewSession:
    """Lock and return the pending session of `reviewer` with its booked area review (INV-1)."""
    return (
        ReviewSession.objects.select_for_update(of=("self", "area_review"))
        .select_related("area_review__merge_request__author")
        .get(
            pk=session_id,
            reviewer=reviewer,
            outcome=ReviewSessionOutcome.PENDING,
            area_review__state=AreaReviewState.BOOKED,
        )
    )


def _close(session: ReviewSession, outcome: str, actor: str) -> None:
    """Set the outcome of a session and write the audit event."""
    session.outcome = outcome
    session.outcome_at = timezone.now()
    session.save(update_fields=["outcome", "outcome_at"])
    record(
        actor,
        f"review_session.{outcome}",
        session,
        {"outcome": ReviewSessionOutcome.PENDING},
        {"outcome": outcome},
    )
