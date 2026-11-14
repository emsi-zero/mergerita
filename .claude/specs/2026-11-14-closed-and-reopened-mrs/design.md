# DESIGN: Closed and reopened MRs

Spec: spec.md · Key: MCL

## Shape

- New module `reviews/closing.py`, in the shape of `reviews/merge_confirmation.py`:
  - `confirm_close(payload)` and `confirm_reopen(payload)`, the outbox handlers that read GitLab first;
  - `close_merge_request(locked_mr)` and `reopen_merge_request(locked_mr, reopened_at)`, the writes under the lock.
- `reviews/lifecycle.py`:
  - The hook actions `close` and `reopen` only queue the jobs.
  - The reconciler-only actions `closed` and `reopened` queue them too, and are ignored when they come from a webhook (MCL-10, IO-4).
  - `_reconcile_merge_request` queues `confirm_reopen` when it lists an MR we hold as `closed` (MCL-9).
- `reviews/reconciler.py` gets a new `_reconcile_closures(project_id)` per project (MCL-8, MCL-9):
  - a full opened listing (`updated_after=None`, all pages);
  - for each tracked MR not `merged` or `closed` and missing from the listing, a GitLab read; when GitLab says `closed`, it stores a `closed` reconcile event;
  - for each tracked `closed` MR that is listed, a `reopened` reconcile event.
- `urgency.expire_pending(merge_request, reason="the MR was merged")` gets the `reason` argument; the close passes "the MR was closed".
- `booking.book_follow_up`: for an unbooked area review, try its most recent reviewer who did not hand it back (`only_reviewer`), then any eligible owner (MCL-7).
- Watchdog `_no_progress` also excludes `closed`. The other readers already filter on open states. Session 2 adds a regression test for each.
- The panel adds a "Closed" status label, and the urgency form is not shown, because `closed` is not in `REQUESTABLE_STATES`.

## Write paths and locks

| Command | Locks, in order | GitLab read before? | Audit | Outbox |
|---|---|---|---|---|
| `closing.confirm_close` → `close_merge_request` | MR row → re-read state → `lock_booking()` → each pending session's AreaReview (pk order) → the ReviewSession → CapacityDay (`release_session_minutes`) → the open MergeAssignment | `get_merge_request` (state must be `closed`) | `mr.closed`, `review_session.superseded`, `area_review.unbooked`, `merge.cancelled`, `urgency.expired` | `notify` (reviewers, merge owner, approver or admins) |
| `closing.confirm_reopen` → `reopen_merge_request` | MR row → re-read state (must be `closed`) | `get_merge_request` (state must be `opened`) | `mr.reopened` | `check_ai_review` |
| reconciler closure pass | none (it stores inbox events only) | listing, plus per-MR reads | — | — |

The close takes `lock_booking()` because it releases CapacityDay minutes
(LOCK-1, LOCK-3). The rebooking and the urgency approval take the MR lock
first, so they serialize with the close (MCL-16, MCL-17). Each then re-reads
the state under the lock and stops on `closed`: the rebooking skips
(`OPEN_STATES`), and the approval is refused (URG-26: "This MR is no longer
open.").

## Decisions

| Decision | Owner | Invariant | Alternative rejected |
|---|---|---|---|
| Close effects in one function under one transaction | closing | INV-1, MCL-5 | Spread over handlers: a partial close is possible |
| Booked area review → `unbooked` with `booking._unbook` (sets `unbooked_since`, audit) | booking | INV-1 | A new `cancelled` state (spec decision) |
| Supersede through `booking.supersede_session` (releases minutes for today or later) | booking | DATA-6 | A raw update: no audit, minutes not released |
| Reopen sets `ai_gate_entered_at = max(old, reopened_at)` and queues `check_ai_review` | closing | GL-2 | Keeping the old entry time |
| The reopen time is GitLab's `updated_at` from the hook, else `timezone.now()` | closing | DATA-2 | Only now() |

## Data and migrations

`reviews/0016_closed_state.py`: an `AlterField` on `MergeRequest.state` for
the new choice. There is no SQL change and no data step.

## Failure modes

| Fault | Behaviour | Criterion |
|---|---|---|
| GitLab read fails in `confirm_close` | The handler raises and the outbox retries with backoff. Nothing has changed | MCL-1, IO-3 |
| GitLab says `merged` on a close hook | `confirm_close` does nothing. The merge path handles it | MCL-1 |
| A close step raises | The whole transaction rolls back | MCL-5 |
| A listing page fails in the closure pass | That project's pass is logged and skipped. The next run tries again | MCL-8, IO-2 |
| A per-MR read fails in the closure pass | That MR is logged and skipped | MCL-8 |
| Close and reopen hooks arrive out of order | Each job reads the current GitLab state, so only the true end state is applied | MCL-1, MCL-6 |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The full opened listing on every reconcile run | Low | A few API calls every 300 s | One call per 100 opened MRs. The per-MR reads only cover MRs missing from the list |
| `book_follow_up` behaviour change for unbooked area reviews after a push | Low | A different reviewer is chosen | It prefers the previous reviewer, as the rebooking does. The existing RVO-8 test still pins the booked path |
