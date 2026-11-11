# DESIGN: Urgency gaps: opted-out merge owners, expiry, reminders, working days

Spec: spec.md · Key: UGF

## Shape

- `merge_router._best_fit` drops candidates whose effective limit is 0 before
  the urgent bypass (UGF-1).
- `merges.record_merge` calls `urgency.expire_pending(merge_request)` right
  after the MR's move to `merged`, in the caller's transaction (UGF-2).
- `urgency._available` adds the working-day check (UGF-3).
- The approver walk is refactored into `_walk(start, author, today, visited)`.
  `resolve_approver` starts it at `author.reports_to`; escalation starts it at
  the current approver's `reports_to`, with the current approver already in
  `visited` (UGF-5, INV-5).
- New `urgency.run_urgency_check(now=None)`: it selects pending requests whose
  current approver has waited past a threshold, then handles each one in its
  own transaction under the MR lock, re-reading the request (IO-2, LOCK-2).
  The worker calls it every `RMO_URGENCY_CHECK_INTERVAL_SECONDS` through
  `run_guarded` (UGF-6).
- `urgency.working_hours(start, end, weekdays)` returns the hours of
  [start, end] that fall on the given weekdays, in local time. The watchdog
  imports it, which avoids an import cycle.
- The watchdog gets an "Urgency pending" case (UGF-8). `Finding` gains an
  optional `detail` string, so this case shows author → approver and hours
  instead of the MR "days" line.

## Write paths and locks

| Command | Locks, in order | GitLab read before? | Audit | Outbox |
|---|---|---|---|---|
| `merges.record_merge` → `urgency.expire_pending` | the caller's MR lock (`confirm_merge`) | the caller's, before the transaction | `urgency.expired` | `notify` (approver, or admins) |
| `urgency.run_urgency_check`, per request | MR row → re-read status, approver, `approver_since`, `reminded_at` (new statement) | none | `urgency.reminded` or `urgency.escalated` | `notify` (approver; old and new approver on escalation) |

Escalation and a decision both take the MR lock first, so they serialize
(UGF-7). A decision that comes after the escalation reads the new approver
in `may_decide` and refuses the old one with DoesNotExist, which gives a 404
(URG-10).

## Decisions

| Decision | Owner | Invariant | Alternative rejected |
|---|---|---|---|
| Effective limit 0 is a candidate filter, before the urgent bypass | merge_router | UGF-1 | Special-casing it in the sort |
| `approver_since` starts both clocks; escalation resets it and clears `reminded_at` | urgency | INV-1 | Separate per-threshold flags |
| Escalation runs only when a named approver exists; with the admins as approver, only reminders are sent | urgency | UGF-5 | Escalating "from the admins" (there is no level above) |
| When both thresholds have passed in one check, escalate (skip the reminder) | urgency | UGF-4/5 | Remind and escalate together |
| Hours are counted on company working days (`RMO_DEFAULT_WORKING_DAYS`) | urgency | UGF-4 | The approver's own days: an approver who is off would never be escalated past |

## Data and migrations

`reviews/0015_urgency_gaps.py`:

- an `AlterField` for the new status choice;
- `UrgencyRequest.approver_since`, `DateTimeField(db_default=Now(), default=timezone.now)`;
- `UrgencyRequest.reminded_at`, nullable;
- `RebookingSettings.urgency_reminder_hours`, `db_default=4`, validators 1–72;
- `RebookingSettings.urgency_escalation_hours`, nullable, `db_default=8`, validators 1–72, and `RebookingSettings.clean()` requires it to be larger than the reminder.

A `RunPython` step (noop reverse) sets `approver_since = created_at` on
existing rows. The tables are small and the worker may keep running.

## Failure modes

| Fault | Behaviour | Criterion |
|---|---|---|
| One request fails in the check | Its transaction rolls back, the error is logged, and the next request is handled | IO-2 |
| The whole check raises | `run_guarded` logs it and alerts once per streak, and the loop goes on | RWF-4/5 |
| Two workers | The MR lock plus the re-read of `reminded_at` and `approver_since` mean the second one does nothing | UGF-6 |
| The approver changed between selection and lock | The re-read thresholds use the new `approver_since`, so nothing happens yet | UGF-6 |
| Escalation finds nobody | The approver becomes None (the admins), and the admins are told | UGF-5 |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The every-300 s check adds load | Low | One query plus a few rows per pass | Select only pending requests past the reminder threshold |
| A chain of escalations that keeps notifying | Low | Noise | Each level gets the full window, and it ends at the admins (no further escalation) |
