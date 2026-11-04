# SPEC: Merge routing daily retry

Key: MRDR · Route: task · Risk: high
Created: 2026-11-04

Risk is high because the retry assigns merges under the global booking lock (concurrency, MRG INV-1) and runs in the worker next to the reconciler.

## Problem

When no merge owner fits an approved MR (MRG-2), the orchestrator alerts the admin once and never offers the MR again. The MR waits in `approved` with nobody to merge it, even after a merge owner comes back from leave, has room under their limit, or is added. The developer chose a daily retry at an admin-set time, a daily summary alert, and a manual retry in the admin.

## Non-goals

- No change to how a merge owner is picked (MRG-1, MRG-3 rules stay as they are).
- No retry of MRs that have an open assignment. A retry never moves an assignment to another owner.
- No retry on every reconcile pass, and no automatic retry when an admin edits a person or a leave.
- No change to the first no-fit alert of MRG-2.
- No escalation after N days. The summary shows how long each MR has waited.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MRDR-1 | WHEN the local time (Asia/Tehran) reaches the daily retry time set in the admin, and the retry has not run yet that day, THE worker SHALL run merge routing once for every MR that is `approved` and has no open merge assignment. An MR that now fits SHALL be assigned as in MRG-1 (window, reason, chat to the merge owner, audit). | CRITICAL BEHAVIOR | An approved MR still waits after a merge owner has become free |
| MRDR-2 | WHILE more than one worker runs, or the worker passes the retry time more than once in a day, THE retry SHALL run at most once per local day. IF the worker was down at the retry time, THEN the retry SHALL run on the worker's first pass later that same day. | IMPORTANT FAILURE MODE | Two workers retry together and send double chats and alerts, or a restart skips a day |
| MRDR-3 | WHEN a retry run ends with one or more MRs still unassigned, THE retry SHALL queue one admin alert that lists each such MR (project, iid, title) and the whole days it has waited since it was approved, and SHALL NOT queue a per-MR no-fit alert. WHEN no MR is still unassigned, no summary SHALL be sent. | BUSINESS RULE | Admins get one alert per MR per day, or are never told an MR is still stuck |
| MRDR-4 | WHEN an admin runs the "Retry merge routing" action on selected merge requests, THE admin SHALL run merge routing for each selected MR that is `approved` and has no open merge assignment, skip the others, and show how many were assigned, how many are still unassigned, and how many were skipped. | CRITICAL BEHAVIOR | After adding a merge owner, the admin must wait until the next day for stuck MRs |
| MRDR-5 | IF an MR stops being eligible (merged, approval revoked, or assigned by another path) between the moment it was selected and the moment it is routed, THEN THE retry SHALL skip it, under the MR lock. | IMPORTANT FAILURE MODE | A retry assigns a second merge owner, or assigns an MR that was just merged or revoked |
| MRDR-6 | IF an admin enters a daily retry time that is not a valid time, THEN THE admin SHALL reject it with an error. | IMPORTANT FAILURE MODE | A typo silently stops the retry |

Not tested, and why:
- The admin action button layout and message styling — FRAMEWORK BEHAVIOR
- The worker loop's sleep timing — covered through the function the loop calls (MRDR-1, MRDR-2), not the loop itself

## Invariants

- INV-1 — A retry never exceeds a merge owner's daily limit (MRG INV-1) — owned by the merge router — enforced by routing each MR under the MR lock, then `lock_booking()`, as coverage does (INV-6 lock order: MR → booking lock).
- INV-2 — An MR has at most one open merge assignment — owned by the merge router — enforced by the check under the MR lock plus the `merge_assignment_one_open` partial unique constraint.
- INV-3 — The daily retry runs at most once per local day — owned by the retry service — enforced by claiming the day on the settings row under `select_for_update` (the `last_retry_on` date) before routing.

## Contracts

- `MergeRoutingSettings` gains `daily_retry_time` (time, default 08:00, in `TIME_ZONE`) and `last_retry_on` (date, nullable, not editable in the admin).
- `MergeRequest` gains `approved_at` (date-time, nullable). Coverage sets it each time the MR moves to `approved`. MRs approved before this change have it empty, and the summary shows their wait as unknown.
- One `reviews` migration (0012), with defaults; no data step.
- `assign_merge` gains a keyword `alert_on_no_fit` (default true). The retry passes false. The `merge.unassigned` audit event is still written, with `"retry": true` in its data.
- The worker calls the retry on every loop pass; it does nothing until the retry time is reached and the day is not yet claimed.
- New admin action on merge requests: "Retry merge routing". The merge request admin stays read-only apart from this action.
- New admin alert text: "<N> approved MR(s) still have no merge owner:" followed by one line per MR: "<project>:<iid> <title> — waiting <D> day(s)" (or "waiting since an unknown date").
- New audit event: `merge.retry_run` (counts of assigned and still unassigned, and the trigger: `daily` or `admin`).

## Validation decisions

- Daily retry time: a valid time — REQUIRED — model field and admin form (MRDR-6).
- Admin action selection: only `approved` MRs with no open assignment are routed; others are skipped and counted — REQUIRED — the retry service, re-checked under the MR lock (MRDR-5).

## Standards that apply

- python_code_style: Naming, Type annotations, Modular design
- django_standards: Configuration and business constants (the retry time is admin-set, DB-driven), Transactions, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| When to retry | Daily at a set time / every reconcile pass | Daily at an admin-set time, default 08:00 Asia/Tehran (developer) |
| Admin alerts after retries | Daily summary / per MR per retry / first failure only | One daily summary of still-unassigned MRs (developer) |
| Manual retry | Admin action / daily only | Admin action on selected MRs (developer) |
| Does the manual action send the summary | Yes / no | No: it shows the counts in the admin message instead |
