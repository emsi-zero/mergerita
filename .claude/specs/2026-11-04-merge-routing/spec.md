# SPEC: Merge routing

Key: MRG · Route: project · Risk: high
Created: 2026-11-04

## Problem

An MR that reaches `approved` has nowhere to go. Nobody is picked to merge it, and nothing checks the rule that the author, the stage 2 approvers and the person who merges are three different people. GitLab cannot enforce that rule: an approver with merge rights can merge. This change is flow 7 of the design, in its detect-only form (decision 6, chosen by the developer).
- The orchestrator assigns a merge owner and a merge time.
- People still merge in GitLab.
- The orchestrator detects each merge, records who did it and when, and alerts on violations.

## Non-goals

- No enforcement. The orchestrator does not merge, and does not change branch protection or any GitLab setting (decision 6: detect only).
- No GitLab assignee change. The merge owner is told in chat and sees the MR in the panel only (developer decision).
- No release candidates, release notes or CTO approval (flow 8).
- No per-area merge ownership. Merge owners can merge any area (developer decision).
- No undo of a violating merge. It is alerted and recorded.
- No change to review booking, coverage or the AI gate.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MRG-1 | WHEN an MR moves to `approved`, THE merge router SHALL assign it to a merge owner who is not its author, not the current reviewer of any of its area reviews, working and not on leave on the window's day, and under their daily merge limit that day, in that person's earliest merge window at or after now within the booking horizon. Ties are broken by fewer assignments that day, then the earlier window, then the lower person id. The assignment SHALL store its window and a reason, chat the merge owner, and write an audit event. | BUSINESS RULE | An approver or the author is asked to merge, or an MR goes to someone who is away or already full |
| MRG-2 | IF no merge owner fits within the horizon, THEN THE merge router SHALL leave the MR unassigned, keep it `approved`, and queue an admin alert. | IMPORTANT FAILURE MODE | An approved MR waits silently with nobody to merge it |
| MRG-3 | WHILE assigning, THE merge router SHALL never give a merge owner more assignments on one day than their daily merge limit: their own limit when set, else the default limit set in the admin. Two MRs approved at the same time SHALL NOT exceed it. | BUSINESS RULE | Two approvals race and overload one merge owner past their limit |
| MRG-4 | WHEN a merge owner opens My merges, THE panel SHALL list their open assignments in window order, each with the MR, its areas, its approvers, the window and a "Go to MR" link; a person who is not a merge owner SHALL not see the tab, and the page SHALL reply 404 for them. | CRITICAL BEHAVIOR | The merge owner does not know what to merge or when |
| MRG-5 | WHEN GitLab reports an MR as merged, by a Merge Request Hook with action `merge` or by reconciliation, THE MR lifecycle SHALL confirm the merge by reading the MR from GitLab (state, `merge_user`, else `merged_by`, and `merged_at`), and only IF GitLab shows it merged, record who merged and when, move the MR to `merged`, and write an audit event. A hook that GitLab does not confirm SHALL change nothing. | CRITICAL BEHAVIOR | Merges are not recorded, so nothing can be checked |
| MRG-6 | IF the person who merged is the MR's author or one of its approvers, THEN THE MR lifecycle SHALL mark the merge as a separation-of-duties violation and queue an admin alert naming the MR, the merger and their role. | SECURITY BOUNDARY | The four-eyes rule is broken and nobody notices |
| MRG-7 | IF an MR is merged while it is not `approved`, or while GitLab no longer shows a fresh approval from the reviewer of one of its approved area reviews (for example an approval revoked just before the merge), THEN THE MR lifecycle SHALL record it as merged before approval and queue an admin alert. | SECURITY BOUNDARY | An MR skips review or the AI gate and nobody notices |
| MRG-8 | WHEN a merge is recorded, THE MR lifecycle SHALL mark it outside its window if it happened before the assigned window or later than the window length after it; this is shown in the audit event and the panel, and SHALL NOT alert. A merge by someone other than the assigned merge owner is recorded the same way, without an alert. When no merge owner was assigned, the merge is not marked as by another person, and the panel shows "no merge owner assigned". | BUSINESS RULE | Merge timing cannot be reported on |
| MRG-9 | WHEN reconciliation runs, THE reconciler SHALL also read each MR that the orchestrator holds as `approved` and, IF GitLab shows it merged, handle it as in MRG-5 to MRG-8. | IMPORTANT FAILURE MODE | A lost merge webhook hides a merge and its violation |
| MRG-10 | IF an admin enters a merge time that is not a valid `HH:MM`, a negative daily limit, or a window length of zero or less, THEN THE admin SHALL reject it with an error. | IMPORTANT FAILURE MODE | A typo silently makes a merge owner unbookable or unlimited |
| MRG-11 | WHILE an MR is `approved` and not merged, THE coverage check SHALL keep running on its hooks and on reconciliation; IF an area review loses its fresh approval, THEN the MR SHALL go back to `in_review` (with the follow-up of RVO-7), its open merge assignment SHALL be cancelled, and the merge owner SHALL be told; a later approval assigns it again (MRG-1). | IMPORTANT FAILURE MODE | An approval revoked before the merge goes unnoticed, and the merge owner merges code that is no longer approved |
| MRG-12 | IF GitLab reports a merge with no merger (neither `merge_user` nor `merged_by`), THEN THE MR lifecycle SHALL record the merge with an unknown merger and queue an admin alert that separation of duties was not checked. | SECURITY BOUNDARY | An auto-merge or a direct push by an approver passes unseen |

Not tested, and why:
- The admin layout — FRAMEWORK BEHAVIOR
- The look of My merges — checked by eye
- CSRF on admin and panel forms — FRAMEWORK BEHAVIOR

## Invariants

- INV-1 — A merge owner's assignments on a day never exceed their effective daily limit — owned by the merge router — enforced by assigning under the global booking lock and counting the assignments for that person and day inside it.
- INV-2 — The merge router never assigns the author or a current reviewer of the MR — owned by the merge router — enforced by candidate exclusion plus a check before save.
- INV-3 — Every recorded merge is checked against the author, the approvers and the approvals GitLab shows when the merge is confirmed — owned by the MR lifecycle — enforced by one outbox job, `confirm_merge`, for webhook and reconciliation merges, which reads GitLab before it records.
- INV-4 — No GitLab call inside a database transaction — owned by the reconciler and `confirm_merge` — enforced by reading GitLab before the atomic block.
- INV-5 — A merge is recorded once — owned by the MR lifecycle — enforced by acting only while the MR is not yet `merged`, under the MR row lock.

## Contracts

- `Person` gains `is_merge_owner` (bool), `merge_times` (list of `HH:MM`, in the app's time zone) and `daily_merge_limit` (nullable; none means the default).
- A new singleton model `MergeRoutingSettings` (admin-editable) holds `default_daily_merge_limit` (default 5) and `merge_window_minutes` (default 30).
- A new model `MergeAssignment`: MR, merge owner, window (date-time), reason, assigned at, merged by (Person, nullable), merged by GitLab user id, merged at, `outside_window`, `by_other_person`, `violation` (`none`, `author`, `approver`, `before_approval`).
- `MergeRequestState` gains `merged`.
- `TIME_ZONE` becomes `Asia/Tehran`. Booking days, "today" and merge windows all follow it (developer decision).
- A new panel page `/panel/merges/` ("My merges"), shown only to merge owners.
- The mock GitLab gains a `merge PROJECT IID USER_ID` control. It sets the MR to merged with `merged_by` and `merged_at`, and sends a Merge Request Hook with action `merge`. The gateway gains `get_merge_request(project_id, iid)` returning state, merged by (`merge_user`, else `merged_by`, else none) and merged at. A malformed MR body raises `GitLabError`.
- New audit events: `merge.assigned`, `merge.unassigned` (no fit), `merge.cancelled`, `mr.merged` (with merger, window flags and violation).
- `MergeAssignment` also has `cancelled_at` (nullable). An assignment is open while it has neither `merged_at` nor `cancelled_at`.
- New chat texts:
  - To the merge owner: "Merge !<iid> <title> in your <time> window on <day>."
  - Admin alerts for no fit, violations and merges before approval.
- One `reviews` migration, plus a data migration that creates the settings row. A second migration (0011) adds a partial unique constraint: at most one open assignment per MR.
- Violation precedence: `author`, then `approver`, then `before_approval`, then `none`.
- `by_other_person` is false when no merge owner was assigned.
- A new outbox job `confirm_merge` (MR id). The merge hook and the reconcile `merged` event only queue it.
- Chat text to the merge owner on cancel: "Do not merge !<iid> <title>: an approval was revoked."
- Admin alert for a merge with an unknown merger.

## Validation decisions

- Merge times: `HH:MM`, 00:00–23:59 — REQUIRED — admin form (MRG-10).
- Daily limit at least 0, and window length at least 1 minute — REQUIRED — admin form (MRG-10). A limit of 0 means the person takes no merges.
- Merger identity: read from GitLab, not the hook payload; matched by GitLab user id to a Person; an unmatched merger is recorded by id only; no merger at all alerts (MRG-12) — REQUIRED — `confirm_merge`.

## Standards that apply

- python_code_style: Naming, Type annotations, Modular design, Constants and module globals
- django_standards: Configuration and business constants (per-person and admin-set limits are DB-driven, the codebase's stated exception), Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- technical_standards: TS-5
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| Enforce or detect | Enforce / detect | Detect only (developer) |
| Who may merge | Per-area role / any code owner / global merge owners | Global merge owners, flagged per person (developer) |
| Notify | Chat + GitLab assignee / chat + panel | Chat + panel, no GitLab change (developer) |
| Daily limit | — | Per merge owner, default and override set in the admin (developer) |
| Merge times | Team-wide / per person | Per merge owner (developer) |
| Time zone | Asia/Tehran / UTC | Asia/Tehran (developer) |
| Merge outside its window | Record / alert / ignore | Record, no alert (developer) |
| Detection | Webhook + reconcile / webhook only | Both (developer) |
| Who counts as an approver | GitLab approvals list / area review reviewers | The current reviewers of the MR's area reviews, the approvals the orchestrator tracks |
| What happens to an assignment when the MR leaves `approved` before merge (for example a revoked approval) | Keep / cancel | Cancel it and tell the merge owner (MRG-11); the router assigns again on the next approval |
