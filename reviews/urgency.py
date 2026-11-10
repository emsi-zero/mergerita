"""Urgency requests: who approves, and the request, withdraw, decline and approve commands.

Every write takes the MR row lock first, then reads the request again in a new
statement (INV-1, LOCK-2).
"""

from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from audit.services import record
from integrations.models import OutboxCall, OutboxStatus
from reviews import area_resolver, booking, rebooking
from reviews.alerts import queue_admin_alert, queue_notification
from reviews.models import (
    AreaReview,
    AreaReviewState,
    Leave,
    MergeRequest,
    MergeRequestState,
    Person,
    ReviewSession,
    ReviewSessionOutcome,
    Team,
    UrgencyRequest,
    UrgencyRequestStatus,
)

REQUESTABLE_STATES = (
    MergeRequestState.AWAITING_AI_REVIEW,
    MergeRequestState.AWAITING_BOOKING,
    MergeRequestState.IN_REVIEW,
    MergeRequestState.APPROVED,
)


class UrgencyRefused(Exception):
    """A command was refused. The message is safe to show to the person."""


def _available(person: Person, today: date) -> bool:
    return not Leave.objects.filter(
        person=person, start_date__lte=today, end_date__gte=today
    ).exists()


def resolve_approver(author: Person, today: date) -> Person | None:
    """Return who decides `author`'s request, or None when the admins decide (URG-8).

    A visited set ends the walk, even on a reporting cycle (INV-5). The author
    is never returned.
    """
    visited = {author.pk}
    candidate = author.reports_to
    while candidate is not None and candidate.pk not in visited:
        visited.add(candidate.pk)
        if _available(candidate, today):
            return candidate
        teams = sorted(
            Team.objects.filter(lead=candidate).select_related("delegate"),
            key=lambda team: (team.pk != author.team_id, team.pk),
        )
        for team in teams:
            delegate = team.delegate
            if (
                delegate is not None
                and delegate.pk not in visited
                and _available(delegate, today)
            ):
                return delegate
        candidate = candidate.reports_to
    return None


def weekly_count(team: Team) -> int:
    """Count the approved requests of the team's members in the last 7 local days (URG-13)."""
    start = timezone.make_aware(
        datetime.combine(timezone.localdate() - timedelta(days=6), time.min)
    )
    return UrgencyRequest.objects.filter(
        status=UrgencyRequestStatus.APPROVED,
        requester__team=team,
        decided_at__gte=start,
    ).count()


def team_status(team: Team) -> tuple[int, bool]:
    """Return the team's weekly count and whether it has reached its limit (URG-13)."""
    count = weekly_count(team)
    limit = team.weekly_urgent_limit
    return count, limit is not None and count >= limit


def _label(merge_request: MergeRequest) -> str:
    return f"{merge_request.project_id}:{merge_request.iid}"


def _notify_approver(urgency: UrgencyRequest, text: str) -> None:
    if urgency.approver is None:
        queue_admin_alert(text)
    else:
        queue_notification([urgency.approver.gitlab_user_id], text)


def request(author: Person, merge_request_id: int, reason: str, actor: str) -> None:
    """Store a pending request for the author's MR and notify its approver (URG-5, 6).

    Raise `UrgencyRefused` when the MR is not the author's, is in another
    state, or already has a pending or approved request.
    """
    with transaction.atomic():
        try:
            merge_request = MergeRequest.objects.select_for_update(of=("self",)).get(
                pk=merge_request_id
            )
        except MergeRequest.DoesNotExist:
            merge_request = None
        # Read state and author again in a new statement under the lock (LOCK-2).
        state, author_id = MergeRequest.objects.filter(pk=merge_request_id).values_list(
            "state", "author_id"
        ).first() or (None, None)
        if (
            merge_request is None
            or author_id != author.pk
            or state not in REQUESTABLE_STATES
        ):
            raise UrgencyRefused("Urgency cannot be requested for this merge request.")
        approver = resolve_approver(author, timezone.localdate())
        try:
            with transaction.atomic():
                urgency = UrgencyRequest.objects.create(
                    merge_request=merge_request,
                    requester=author,
                    approver=approver,
                    reason=reason,
                )
        except IntegrityError as error:
            raise UrgencyRefused("Urgency is already requested for this MR.") from error
        record(
            actor,
            "urgency.requested",
            urgency,
            None,
            {
                "merge_request_id": merge_request.pk,
                "approver_id": approver.pk if approver else None,
                "reason": reason,
            },
        )
        _notify_approver(
            urgency,
            f"Urgency requested for MR {_label(merge_request)} by {author.name}: "
            f"{reason}. Decide in the panel.",
        )


def _lock_request(request_id: int) -> tuple[MergeRequest, UrgencyRequest]:
    """Lock the request's MR, then read the request again (INV-1).

    Raise `UrgencyRequest.DoesNotExist` when there is no such request.
    """
    merge_request_id = UrgencyRequest.objects.values_list(
        "merge_request_id", flat=True
    ).get(pk=request_id)
    merge_request = MergeRequest.objects.select_for_update(of=("self",)).get(
        pk=merge_request_id
    )
    urgency = UrgencyRequest.objects.select_related("requester", "approver").get(
        pk=request_id
    )
    return merge_request, urgency


def withdraw(author: Person, request_id: int, actor: str) -> None:
    """Withdraw the author's pending request and tell the approver (URG-7).

    Raise `UrgencyRequest.DoesNotExist` for another person's request and
    `UrgencyRefused` when it is no longer pending.
    """
    with transaction.atomic():
        merge_request, urgency = _lock_request(request_id)
        if urgency.requester_id != author.pk:
            raise UrgencyRequest.DoesNotExist
        if urgency.status != UrgencyRequestStatus.PENDING:
            raise UrgencyRefused("This request is no longer pending.")
        urgency.status = UrgencyRequestStatus.WITHDRAWN
        urgency.save(update_fields=["status"])
        record(
            actor,
            "urgency.withdrawn",
            urgency,
            {"status": UrgencyRequestStatus.PENDING},
            {"status": UrgencyRequestStatus.WITHDRAWN},
        )
        _notify_approver(
            urgency,
            f"The urgency request for MR {_label(merge_request)} was withdrawn.",
        )


def may_decide(person: Person, urgency: UrgencyRequest) -> bool:
    """True when `person` is the approver or an admin, and is not the requester (URG-9, 10)."""
    return urgency.requester_id != person.pk and (
        person.is_admin or urgency.approver_id == person.pk
    )


def _lock_decidable(
    person: Person, request_id: int
) -> tuple[MergeRequest, UrgencyRequest, Person]:
    """Lock and re-read the request and the deciding person's rights (URG-10, 12).

    Raise `UrgencyRequest.DoesNotExist` when `person` may not decide it and
    `UrgencyRefused` when it is no longer pending.
    """
    merge_request, urgency = _lock_request(request_id)
    person = Person.objects.get(pk=person.pk)
    if not may_decide(person, urgency):
        raise UrgencyRequest.DoesNotExist
    if urgency.status != UrgencyRequestStatus.PENDING:
        raise UrgencyRefused("This request is no longer pending.")
    return merge_request, urgency, person


def decidable_requests(person: Person) -> QuerySet[UrgencyRequest]:
    """Return the pending requests `person` may decide: their own approvals, or all for an admin."""
    pending = UrgencyRequest.objects.filter(
        status=UrgencyRequestStatus.PENDING
    ).exclude(requester=person)
    return pending if person.is_admin else pending.filter(approver=person)


def _decide(
    urgency: UrgencyRequest, person: Person, status: str, note: str, actor: str
) -> None:
    urgency.status = status
    urgency.decided_by = person
    urgency.decided_at = timezone.now()
    urgency.note = note
    urgency.save(update_fields=["status", "decided_by", "decided_at", "note"])
    record(
        actor,
        f"urgency.{status}",
        urgency,
        {"status": UrgencyRequestStatus.PENDING},
        {"status": status, "decided_by_id": person.pk, "note": note},
    )


def decline(person: Person, request_id: int, note: str, actor: str) -> None:
    """Decline a pending request and tell the author; bookings do not change (URG-11)."""
    with transaction.atomic():
        merge_request, urgency, person = _lock_decidable(person, request_id)
        _decide(urgency, person, UrgencyRequestStatus.DECLINED, note, actor)
        text = f"Your urgency request for MR {_label(merge_request)} was declined"
        queue_notification(
            [urgency.requester.gitlab_user_id],
            f"{text}: {note}" if note else f"{text}.",
        )


def approve(person: Person, request_id: int, note: str, actor: str) -> None:
    """Approve a pending request: mark the MR urgent, tell the author, check the team limit.

    URG-9, 12, 13. The booking effect is `_apply_urgency` (URG-14).
    """
    with transaction.atomic():
        merge_request, urgency, person = _lock_decidable(person, request_id)
        state = MergeRequest.objects.values_list("state", flat=True).get(
            pk=merge_request.pk
        )
        if state not in REQUESTABLE_STATES:
            raise UrgencyRefused("This MR is no longer open.")
        _decide(urgency, person, UrgencyRequestStatus.APPROVED, note, actor)
        merge_request.urgent_at = timezone.now()
        merge_request.save(update_fields=["urgent_at"])
        _apply_urgency(merge_request)
        queue_notification(
            [urgency.requester.gitlab_user_id],
            f"Your urgency request for MR {_label(merge_request)} was approved.",
        )
        team = urgency.requester.team
        if team is not None and team.weekly_urgent_limit is not None:
            count = weekly_count(team)
            if count > team.weekly_urgent_limit:
                queue_admin_alert(
                    f"Team {team.name} has {count} approved urgent requests in 7 days "
                    f"(limit {team.weekly_urgent_limit})."
                )


def _apply_urgency(merge_request: MergeRequest) -> None:
    """Book today each area review that is unbooked or not booked today (URG-14, 17, 27).

    The caller holds the MR lock and has saved `urgent_at`. Lock order (INV-2):
    `lock_booking()`, area reviews by pk, their sessions, then the capacity
    rows inside the booking. An MR still in the AI gate, or with a pending
    follow-up job, only keeps `urgent_at`: the resolver books it urgently
    later (URG-19, 27). One alert lists the area reviews that are not booked
    today (URG-18).
    """
    if (
        merge_request.state == MergeRequestState.AWAITING_BOOKING
        and OutboxCall.objects.filter(
            kind="resolve_areas",
            status__in=(OutboxStatus.PENDING, OutboxStatus.IN_FLIGHT),
            payload__merge_request_id=merge_request.pk,
        ).exists()
    ):
        return
    booking.lock_booking()
    today = timezone.localdate()
    notes: list[str] = []
    area_reviews = (
        AreaReview.objects.select_for_update(of=("self",))
        .filter(merge_request=merge_request)
        .exclude(state=AreaReviewState.APPROVED)
        .order_by("pk")
    )
    for area_review in area_reviews:
        area_review.merge_request = merge_request
        if area_review.state == AreaReviewState.BOOKED:
            session = (
                ReviewSession.objects.select_for_update(of=("self",))
                .filter(area_review=area_review, outcome=ReviewSessionOutcome.PENDING)
                .first()
            )
            if session is None or session.day == today:
                continue
            booking.supersede_session(session)
        rebooking.book_with_continuity(
            area_review, alert_on_no_fit=True, urgent_notes=notes
        )
    if notes:
        booking.queue_urgent_alert(merge_request, "\n".join(notes))
    if (
        merge_request.state == MergeRequestState.AWAITING_BOOKING
        and merge_request.area_reviews.exists()
        and not merge_request.area_reviews.filter(
            state=AreaReviewState.UNBOOKED
        ).exists()
    ):
        area_resolver.mark_in_review(merge_request)
