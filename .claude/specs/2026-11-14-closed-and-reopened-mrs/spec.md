# SPEC: Closed and reopened MRs

Key: MCL · Route: project · Risk: high
Created: 2026-11-14

Risk is high: a new terminal MR state decided from a GitLab read (GL-1), a
close that writes bookings, capacity, merge assignments and urgency in one
transaction under the MR lock (LOCK-1, TX-1), races with the urgency approval
and the daily rebooking (LOCK-6), a reconciler change, and a migration.

## Problem

Lifecycle ignores the Merge Request Hook actions `close` and `reopen`, and
the reconciler lists only opened MRs. So an MR closed without merging keeps
everything it had:

- its booked sessions and their CapacityDay minutes;
- its open merge assignment;
- its pending urgency request, which keeps escalating.

The watchdog then reports it every day. A reopened MR is not picked up
again either.

## Non-goals

- No new GitLab write (GL-3). Reviewers set in GitLab are not removed on close.
- No new area review state: closed MRs are excluded by the MR state (decision below).
- No undo of history: superseded sessions, cancelled assignments and expired
  urgency requests stay as they are after a reopen.
- No detection of merges lost for MRs that were not `approved` (the existing
  merge reconcile covers approved MRs only). That is a separate item.
- No change to `merged`: it stays terminal and is handled as today.

## Acceptance criteria

### Close

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MCL-1 | WHEN a `close` hook arrives for a tracked MR that is not `merged` or `closed`, THE system SHALL read the MR from the GitLab API outside any transaction, and only when GitLab says `closed` SHALL it move the MR to `closed` and write `mr.closed`. When GitLab says anything else, nothing SHALL change. | INTEGRATION CONTRACT | A forged or stale hook closes an open MR |
| MCL-2 | WHEN an MR is closed, THE close SHALL supersede every pending review session (audit `review_session.superseded`), release its minutes when its day is today or later, move each booked area review to `unbooked`, leave approved area reviews as they are, and notify each reviewer whose session was cancelled. | CRITICAL BEHAVIOR | A closed MR holds reviewers' days and capacity, and the reviewers come for nothing |
| MCL-3 | WHEN an MR is closed with an open merge assignment, THE close SHALL cancel it with the existing cancel message and reason "the MR was closed". | BUSINESS RULE | A merge owner keeps a window for an MR that will never merge |
| MCL-4 | WHEN an MR is closed with a pending urgency request, THE close SHALL expire it, telling the approver "...expired: the MR was closed.". An approved urgency request SHALL stay approved. | BUSINESS RULE | A request on a closed MR keeps escalating |
| MCL-5 | IF any step of the close fails, THEN THE close SHALL change nothing: the state, the sessions, the capacity, the assignment and the request are all written in one transaction, or none is. | IMPORTANT FAILURE MODE | A half-closed MR: closed with sessions still booked, or sessions released on an open MR |

### Reopen

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MCL-6 | WHEN a `reopen` hook arrives for an MR we hold as `closed`, THE system SHALL read the MR from the GitLab API outside any transaction. Only when GitLab says `opened` SHALL it move the MR to `awaiting_ai_review`, set `ai_gate_entered_at` to the reopen time, write `mr.reopened`, and queue the AI check, as a push does. Old sessions, cancelled assignments and expired requests SHALL stay as history, and an approved urgency SHALL stay approved. | INTEGRATION CONTRACT | A reopened MR stays closed, or an old AI verdict from before the close lets it skip the AI gate |
| MCL-7 | WHEN a reopened MR passes the AI gate again, THE follow-up SHALL book each unbooked area review preferring its most recent reviewer who did not hand it back, then any eligible owner. | BUSINESS RULE | A reopened MR loses the reviewers who already know it |

### Reconciliation

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MCL-8 | WHEN reconciliation runs, THE reconciler SHALL list every opened MR of the project (no cursor). For each MR we hold that is not `merged` or `closed` and is missing from that list, it SHALL read the MR from the API one by one, and when GitLab says `closed` it SHALL close it through the same confirmed path as MCL-1. A read that fails SHALL be logged and skipped. | IMPORTANT FAILURE MODE | An MR closed while hooks were lost stays booked forever |
| MCL-9 | WHEN reconciliation lists an MR we hold as `closed` among the opened MRs, THE reconciler SHALL reopen it through the same confirmed path as MCL-6. | IMPORTANT FAILURE MODE | An MR reopened while hooks were lost stays closed |
| MCL-10 | IF a webhook (not the reconciler) sends the reconciler-only actions `closed` or `reopened`, THEN THE system SHALL ignore them, as it ignores `merged`. | SECURITY BOUNDARY | A webhook triggers the reconciler's path |

### Exclusion of closed MRs

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MCL-11 | THE watchdog SHALL report no closed MR in any case. | REGRESSION | A closed MR is reported as stuck every day |
| MCL-12 | THE daily rebooking SHALL not book an area review of a closed MR. | REGRESSION | A reviewer is booked for a closed MR |
| MCL-13 | THE daily merge retry SHALL not assign a closed MR. | REGRESSION | A merge owner is asked to merge a closed MR |
| MCL-14 | THE urgency check SHALL not remind or escalate a request of a closed MR. | REGRESSION | An approver is chased about a closed MR |
| MCL-15 | THE panel's My MRs, My reviews and My merges SHALL not list a closed MR's sessions or assignments, and My MRs SHALL show a closed MR as "Closed" with no urgency form. | REGRESSION | People see work for a closed MR |

### Concurrency

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MCL-16 | WHEN a close and an urgency approval of the same MR happen at the same time, THE system SHALL end with the MR closed and no pending session, whichever runs first. If the close is first, the approval is refused with "This MR is no longer open." If the approval is first, its sessions are superseded by the close. | CONCURRENCY | An urgent booking stays alive on a closed MR |
| MCL-17 | WHEN a close and the daily rebooking handle the same MR at the same time, THE system SHALL end with the MR closed and no pending session. | CONCURRENCY | The rebooking books a reviewer for an MR that just closed |

### Mock

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MCL-18 | THE mock GitLab SHALL offer `close` and `reopen` commands that set the MR's state and send the Merge Request Hook with action `close` or `reopen` and the GitLab headers. | INTEGRATION CONTRACT | The scenario cannot be run against the mock |

Not tested, and why:
- The mock's web UI text — none exists; MCL-18 covers the commands.

## Invariants

- Holds: LOCK-1, LOCK-2, LOCK-3, LOCK-5, LOCK-6, TX-1, TX-2, TX-3, IO-1,
  IO-2, IO-3, IO-4, GL-1, GL-3, GL-4, SEP-1, DATA-1, DATA-2, DATA-3, DATA-5,
  and URG INV-1 (urgency writes take the MR lock first).
- INV-1 — a closed MR has no pending review session, no open merge
  assignment and no pending urgency request — owned by `reviews.closing` —
  enforced by doing all of it in the close transaction, MR lock →
  `lock_booking()` → AreaReview → ReviewSession → CapacityDay.
- INV-2 — `closed` is decided only from a GitLab read, never from a hook
  payload — owned by `reviews.closing.confirm_close` / `confirm_reopen` —
  enforced by the outbox handlers (the `confirm_merge` shape).
- INV-3 — every writer that books, routes or reminds re-reads the MR state
  under the MR lock and treats `closed` as not open — owned by each writer —
  enforced by the existing state re-reads (`OPEN_STATES`, `REQUESTABLE_STATES`,
  `_eligible`).

## Contracts

- Migration `reviews` 0016: `MergeRequestState.CLOSED = "closed"` (choices only; no data step).
- New outbox jobs `confirm_close` and `confirm_reopen`, registered in `reviews/apps.py`.
- Lifecycle handles the hook actions `close` and `reopen`, and the reconciler-only actions `closed` and `reopened`.
- The reconciler adds a per-project closure pass: a full opened listing plus one-by-one reads of the missing tracked MRs.
- `urgency.expire_pending(merge_request, reason)` takes a reason: "the MR was merged" or "the MR was closed".
- `booking.book_follow_up` on an unbooked area review prefers its most recent non-handed-back reviewer (MCL-7).
- Audit actions: `mr.closed`, `mr.reopened`; reused: `review_session.superseded`, `area_review.unbooked`, `merge.cancelled`, `urgency.expired`.
- Chat texts:
  - to each cancelled reviewer: `Your review of MR <p>:<iid> on <day> is cancelled: the MR was closed.`
  - to the merge owner: the existing `Do not merge !<iid> <title>: the MR was closed.`
  - to the approver: `The urgency request for MR <p>:<iid> expired: the MR was closed.`
- Mock: `scripts/gitlab-mock close PROJECT IID` and `reopen PROJECT IID`.
- Panel: a "Closed" status label on My MRs.

## Validation decisions

- The MR state is re-read under the MR lock before the close and the reopen act — REQUIRED (LOCK-2, INV-2).

## Docs

- README: what a close and a reopen do, and that the reconciler catches lost hooks.
- `docs/testing.md` §4 scenario **S** "Closed and reopened MRs": close a booked MR through the mock, see the sessions cancelled and the minutes free, reopen it, see it go through the AI gate and be booked again with the same reviewer. Marked "(unverified)".
- `.claude/rmo/INVARIANTS.md`: add GL-6 ("`closed` and its undo are decided from a GitLab read only") and DATA-6 ("a closed MR holds no pending session, open assignment or pending request"). The next scenario letter becomes T.

## Checkpoint

`.claude/rmo/bin/rmo gate-a` (tests `scripts/test.sh -q`, pylint, isort, black, makemigrations --check, trace)

## Open questions

| Question | Options | Decision |
|---|---|---|
| Area reviews of a closed MR | (a) a new `cancelled` state; (b) keep them, booked ones become `unbooked`, rely on the MR state | (b): every reader that books, routes, reminds or reports already filters on the MR state (`OPEN_STATES`, `REQUESTABLE_STATES`, `_eligible`), so `closed` excludes them for free. A new state would touch every reader of area review state and the follow-up path on reopen |
| How the reconciler finds closes with lost hooks | (a) read every tracked open MR from the API each pass; (b) a full opened listing, then read only the missing ones | (b): one listing per 100 opened MRs plus a read per missing MR, instead of a read per tracked MR. A listing that misses an MR (paging while MRs change) only causes an extra read, never a wrong close, because each candidate is confirmed by its own GitLab read |
| Reopen: which AI gate time | (a) the reopen time; (b) keep the old one | (a) the reopen time, as a push sets the push time: a verdict from before the close does not count |
| Continuity after reopen | (a) prefer the previous reviewer for an unbooked area review in the follow-up; (b) any owner | (a), with the same rule as the daily rebooking (most recent non-handed-back reviewer, then anyone). This also applies to an unbooked area review's follow-up after a push, which is a small behaviour change in the right direction |
| Reconcile cadence for the closure pass | (a) every reconcile run (300 s); (b) daily | (a): the closure pass costs few calls (b above) |
