# SPEC: Urgency gaps: opted-out merge owners, expiry, reminders, working days

Key: UGF · Route: project · Risk: high
Created: 2026-11-11

Risk is high: a new recurring job in the worker loop (IO-2), an escalation
that changes who may decide a request under the MR lock (AUTH-7, LOCK-2), a
write in the merge transaction (TX-1), and a migration.

## Problem

The review of the URG spec found four gaps:

- **Opted-out merge owners.** The README says a daily merge limit of 0 means
  the person takes no merges, but urgent routing ignores the limit entirely,
  so an opted-out owner gets urgent merges.
- **Requests left after a merge.** A pending urgency request stays pending
  after its MR merges. It sits in the approver's tab, and nobody can do
  anything useful with it.
- **Unanswered requests.** Nobody notices an urgency request that nobody
  answers, and urgency needs answers in hours, not days.
- **Approvers on a day off.** Approver resolution checks leave but not
  working days, so a request made on a Thursday waits for an approver who
  is off.

## Non-goals

- No change to who may be a merge owner apart from the effective limit of 0;
  urgent routing still exceeds a limit of 1 or more (URG-23).
- No expiry for approved requests: an approved request stays as the record of
  the decision after the merge.
- No business-hours calendar. Hours are counted only on the company's
  working days, all 24 hours of each.
- No automatic approval or decline after a timeout. Escalation changes the
  approver, never the decision.
- No chat buttons and no panel change beyond the tab no longer listing an
  expired request.

## Acceptance criteria

### Session 1 — routing, expiry, working days

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| UGF-1 | WHILE a merge owner's effective daily merge limit is 0 (their own, or the default when theirs is empty), THE merge router SHALL never choose them, urgent or not. WHILE it is 1 or more, an urgent MR MAY still exceed it (URG-23). | REGRESSION | A person who opted out of merging is asked to merge an urgent MR |
| UGF-2 | WHEN an MR moves to `merged`, THE merge recording SHALL, in the same transaction, set its pending urgency request to `expired`, write `urgency.expired` (actor `orchestrator`), and tell the approver (or the admins when there is none). The Urgency requests tab SHALL NOT list it. | BUSINESS RULE | A request for a merged MR waits in the tab forever, or is approved after the merge |
| UGF-3 | WHEN the approver is resolved, THE resolution SHALL treat a person as unavailable when today (Asia/Tehran) is not one of their working days, as it does for leave, for the manager and the delegate alike. | BUSINESS RULE | A request made on a Thursday waits for an approver who is off until Saturday |

### Session 2 — unanswered requests

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| UGF-4 | WHEN a pending request has waited longer than the reminder threshold since its current approver was assigned, counting only hours on company working days, THE urgency check SHALL remind that approver once (or every admin when there is none) and write `urgency.reminded`. | BUSINESS RULE | An approver forgets an urgent request |
| UGF-5 | WHEN a pending request has waited longer than the escalation threshold since its current approver was assigned, THE urgency check SHALL move it one level up the reporting line, using the approver resolution from the current approver's own manager (never the author; when nobody is found, the admins), tell the old and the new approver, write `urgency.escalated`, and restart both clocks for the new approver. | BUSINESS RULE | A request stays with an approver who never answers |
| UGF-6 | WHILE the worker runs, THE urgency check SHALL run every `RMO_URGENCY_CHECK_INTERVAL_SECONDS`, guarded like the other scheduled jobs. Two workers or two passes SHALL remind or escalate a request at most once per threshold. | CONCURRENCY | A request is escalated twice, or the approver is reminded on every pass |
| UGF-7 | WHEN a request is escalated while its old approver is deciding it, THE system SHALL apply exactly one of the two. A decision by the old approver after the escalation SHALL be refused with 404 (URG-10), and the new approver can decide. | CONCURRENCY | The old approver approves a request that now belongs to someone else, or both happen |
| UGF-8 | WHEN the daily watchdog runs, THE watchdog SHALL list under "Urgency pending" every request pending longer than the reminder threshold, with the MR, the author, the current approver and the hours waited. | BUSINESS RULE | Admins never see that urgency requests are stuck |
| UGF-9 | IF an admin sets a reminder threshold outside 1–72 hours, or an escalation threshold that is not empty and not larger than the reminder threshold, THEN THE settings admin SHALL reject it. | BUSINESS RULE | An escalation that fires before the reminder, or one every hour |

Not tested, and why:
- The exact text of each chat message beyond the Contracts strings — covered
  through the outbox rows in UGF-2, 4 and 5.

## Invariants

- Holds: LOCK-1, LOCK-2, LOCK-5, LOCK-6, TX-1, TX-2, TX-3, IO-2, SEP-3,
  AUTH-7, DATA-1, DATA-2, DATA-5, and URG INV-1 (every request write takes the
  MR lock first) and INV-5 (approver resolution ends, never returns the
  author).
- INV-1 — a request is reminded at most once and escalated at most once per
  approver assignment — owned by `reviews.urgency` — enforced by
  `reminded_at` and `approver_since`, re-read after the MR lock.
- INV-2 — the expiry commits with the MR's move to `merged` or not at all —
  owned by `reviews.merges.record_merge` — enforced by the caller's transaction.
- INV-3 — `urgency_one_active` still covers only pending and approved, so an
  expired request never blocks anything — owned by the model.

## Contracts

- Migration `reviews` 0015:
  - `UrgencyRequestStatus.EXPIRED = "expired"`;
  - `UrgencyRequest.approver_since` (`db_default=Now()`) and `UrgencyRequest.reminded_at` (nullable);
  - on `RebookingSettings` (the scheduler settings row), `urgency_reminder_hours`
    (default 4) and `urgency_escalation_hours` (nullable, default 8), with validators;
  - a data step that sets `approver_since = created_at` for existing rows.
- New setting `RMO_URGENCY_CHECK_INTERVAL_SECONDS` (default 300).
- New worker job `urgency_check`, run through `run_guarded`.
- Audit actions: `urgency.expired`, `urgency.reminded`, `urgency.escalated`.
- Chat texts:
  - expired, to the approver: `The urgency request for MR <p>:<iid> expired: the MR was merged.`
  - reminder: `Reminder: urgency for MR <p>:<iid> by <author> has waited <h> hours. Decide in the panel.`
  - escalated, to the new approver: `Urgency requested for MR <p>:<iid> by <author> was escalated to you: <reason>. Decide in the panel.`
  - escalated, to the old approver: `The urgency request for MR <p>:<iid> was moved to <name>.`
  - watchdog case: `Urgency pending (n):` with lines `<p>:<iid> <title> — <author> → <approver or admins> — <h> hours`.
- README, "Teams and urgency": the limit-0 rule, expiry, the reminder and
  escalation thresholds, and working days in approver resolution.

## Validation decisions

- Thresholds 1–72 hours; escalation empty or larger than the reminder — USEFUL — model validators and `RebookingSettings.clean()`.
- Request status, approver and `approver_since` are re-read after the MR lock in the check — REQUIRED (LOCK-2, INV-1).

## Docs

- `docs/testing.md` scenario R gains steps for an expired request, a reminder
  and an escalation (settings at 1 and 2 hours), and a request on a Thursday.
- README as in Contracts.

## Checkpoint

`.claude/rmo/bin/rmo gate-a` (tests `scripts/test.sh -q`, pylint, isort, black, makemigrations --check, trace)

## Open questions

| Question | Options | Decision |
|---|---|---|
| A closed request's status | (a) new `expired`; (b) `withdrawn` with actor orchestrator | (a) `expired`. Withdrawn means the author changed their mind; reports and the audit should tell the two apart |
| Threshold unit | (a) working days; (b) hours | (b) hours, counted only on company working days (all 24 hours of each, Asia/Tehran). Urgency needs hours; a day off does not count against the approver |
| After the second threshold | (a) move one level up; (b) only alert admins | (a) one level up, resolved from the current approver's own manager with the same leave and working-day rules. When nobody is found, the request goes to the admins. Admins can already decide any request, so it never gets stuck |
| How often escalation can repeat | (a) once; (b) each time the threshold passes again | (b): each approver gets the full window, so a chain of absent managers ends at the admins |
| Where the hour thresholds live | (a) `RebookingSettings`; (b) a new row | (a): it is already the scheduler settings row (run time, watchdog thresholds), edited on one admin page |
| Cadence | (a) inside the daily watchdog; (b) its own interval | (b) every `RMO_URGENCY_CHECK_INTERVAL_SECONDS` (300). The daily watchdog only lists them for admins (UGF-8) |
