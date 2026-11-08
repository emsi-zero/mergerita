"""Daily watchdog for MRs that stopped moving (RBW-12..19).

It only reports and reminds. It saves no MR, area review, session or
assignment (INV-4).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from audit.services import record
from reviews.alerts import queue_admin_alert, queue_notification
from reviews.models import (
    AreaReview,
    AreaReviewState,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    RebookingSettings,
    ReviewSession,
    ReviewSessionOutcome,
)
from reviews.rebooking import claim_day

OPEN_STATES = (MergeRequestState.AWAITING_BOOKING, MergeRequestState.IN_REVIEW)


@dataclass
class Finding:
    """One stuck MR, with its whole working days stuck."""

    merge_request: MergeRequest
    days: int
    sessions: list[ReviewSession] = field(default_factory=list)


def working_days(start: date, today: date, weekdays: Iterable[int]) -> int:
    """Count the days d with start < d <= today and d.weekday() in weekdays (RBW-19)."""
    weekdays = set(weekdays)
    return sum(
        1
        for offset in range(1, (today - start).days + 1)
        if (start + timedelta(days=offset)).weekday() in weekdays
    )


def run_daily_watchdog(now: datetime | None = None) -> dict[str, int] | None:
    """Run the watchdog once per local day, after the day's rebooking.

    The day is claimed on the settings row under `select_for_update` before
    any report is made (INV-1, RBW-12). Return the count per case, or None
    when nothing ran.
    """
    if not claim_day("last_watchdog_on", _is_due, now):
        return None
    return _report(RebookingSettings.load(), timezone.localdate(now))


def _report(settings_row: RebookingSettings, today: date) -> dict[str, int]:
    found: list[tuple[str, str, list[Finding]]] = []
    listed: set[int] = set()
    for key, label, finder in _cases(listed):
        findings = finder(settings_row, today)
        listed.update(finding.merge_request.pk for finding in findings)
        found.append((key, label, findings))
    counts = {key: len(findings) for key, _, findings in found}
    with transaction.atomic():
        record("orchestrator", "watchdog.run", settings_row, None, counts)
        if any(counts.values()):
            queue_admin_alert(_summary(found))
        for key, _, findings in found:
            if key == "missed_slot":
                _remind(findings)
    return counts


def _remind(findings: list[Finding]) -> None:
    """Send each reviewer a chat for every missed session (RBW-14)."""
    for finding in findings:
        for session in finding.sessions:
            queue_notification(
                [session.reviewer.gitlab_user_id],
                f"Your review of MR {_ref(finding.merge_request)} was booked on "
                f"{session.day} and has no outcome. Mark it done, ask for more "
                "time, or hand it back in the panel.",
            )


def _cases(
    listed: set[int],
) -> list[tuple[str, str, Callable[[RebookingSettings, date], list[Finding]]]]:
    """The cases in report order. `listed` fills as they run, for the catch-all."""
    return [
        ("no_ai_verdict", "No AI verdict", _no_ai_verdict),
        ("missed_slot", "Missed slot", _missed_slot),
        ("needs_triage", "Needs triage", _needs_triage),
        (
            "no_progress",
            "No progress",
            lambda row, today: _no_progress(row, today, listed),
        ),
    ]


def _company_days(start: datetime, today: date) -> int:
    return working_days(
        timezone.localdate(start), today, settings.RMO_DEFAULT_WORKING_DAYS
    )


def _no_ai_verdict(row: RebookingSettings, today: date) -> list[Finding]:
    findings = []
    for merge_request in MergeRequest.objects.filter(
        state=MergeRequestState.AWAITING_AI_REVIEW
    ).order_by("pk"):
        days = _company_days(merge_request.ai_gate_entered_at, today)
        if days > row.ai_review_threshold_days:
            findings.append(Finding(merge_request, days))
    return findings


def _missed_slot(_: RebookingSettings, today: date) -> list[Finding]:
    """One finding per MR: the days of its oldest missed session, and all its sessions."""
    sessions = (
        ReviewSession.objects.filter(
            outcome=ReviewSessionOutcome.PENDING,
            day__lt=today,
            area_review__state=AreaReviewState.BOOKED,
            area_review__merge_request__state__in=OPEN_STATES,
        )
        .select_related("reviewer", "area_review__merge_request")
        .order_by("day", "pk")
    )
    by_mr: dict[int, Finding] = {}
    for session in sessions:
        merge_request = session.area_review.merge_request
        if merge_request.pk in by_mr:
            by_mr[merge_request.pk].sessions.append(session)
        else:
            days = working_days(session.day, today, session.reviewer.working_days)
            by_mr[merge_request.pk] = Finding(merge_request, days, [session])
    return sorted(by_mr.values(), key=lambda finding: finding.merge_request.pk)


def _needs_triage(row: RebookingSettings, today: date) -> list[Finding]:
    findings = []
    for merge_request in MergeRequest.objects.filter(
        state=MergeRequestState.NEEDS_TRIAGE
    ).order_by("pk"):
        days = _company_days(merge_request.state_changed_at, today)
        if days > row.triage_threshold_days:
            findings.append(Finding(merge_request, days))
    return findings


def _no_progress(
    row: RebookingSettings, today: date, listed: set[int]
) -> list[Finding]:
    open_assignment = MergeAssignment.objects.filter(
        merge_request=OuterRef("pk"), merged_at__isnull=True, cancelled_at__isnull=True
    )
    unbooked = AreaReview.objects.filter(
        merge_request=OuterRef("pk"), state=AreaReviewState.UNBOOKED
    )
    candidates = (
        MergeRequest.objects.exclude(
            state__in=(MergeRequestState.MERGED, MergeRequestState.WAITING_ON_AUTHOR)
        )
        .exclude(Q(state=MergeRequestState.APPROVED) & ~Exists(open_assignment))
        .exclude(Exists(unbooked))
        .exclude(pk__in=listed)
        .order_by("pk")
    )
    findings = []
    for merge_request in candidates:
        days = _company_days(merge_request.state_changed_at, today)
        if days > row.stale_threshold_days:
            findings.append(Finding(merge_request, days))
    return findings


def _is_due(settings_row: RebookingSettings, local: datetime) -> bool:
    return (
        local.time() >= settings_row.daily_run_time
        and settings_row.last_rebooking_on == local.date()
        and settings_row.last_watchdog_on != local.date()
    )


def _ref(merge_request: MergeRequest) -> str:
    return f"{merge_request.project_id}:{merge_request.iid}"


def _summary(found: list[tuple[str, str, list[Finding]]]) -> str:
    total = sum(len(findings) for _, _, findings in found)
    lines = [f"Stuck MRs ({total}):"]
    for _, label, findings in found:
        if not findings:
            continue
        lines.append(f"{label} ({len(findings)}):")
        for finding in findings:
            merge_request = finding.merge_request
            url = f" {merge_request.web_url}" if merge_request.web_url else ""
            lines.append(
                f"{_ref(merge_request)} {merge_request.title}{url} — "
                f"{finding.days} working day(s)"
            )
    return "\n".join(lines)
