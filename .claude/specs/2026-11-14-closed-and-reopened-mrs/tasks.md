# TASKS: Closed and reopened MRs

Spec: spec.md · Key: MCL · Route: project · State: pending
Checkpoint: `.claude/rmo/bin/rmo gate-a`

## Session 1 — The closed state, confirmed close and reopen, and the races

State: pending
Covers: MCL-1, MCL-2, MCL-3, MCL-4, MCL-5, MCL-6, MCL-7, MCL-10, MCL-16, MCL-17

- [ ] T1 — `MergeRequestState.CLOSED` and migration `reviews/0016_closed_state.py`
- [ ] T2 — `reviews/closing.py`: `confirm_close`, `close_merge_request`, `confirm_reopen`, `reopen_merge_request`, registered in `reviews/apps.py` — MCL-1..6
- [ ] T3 — `reviews/lifecycle.py`: the hook actions `close` and `reopen`, and the reconciler-only actions `closed` and `reopened` (ignored from webhooks) — MCL-1, MCL-6, MCL-10
- [ ] T4 — `urgency.expire_pending(reason=...)`, and `booking.book_follow_up` continuity for unbooked area reviews — MCL-4, MCL-7
- [ ] T5 — tests in `reviews/tests/test_closing.py`: MCL-1..7 and MCL-10 (TestCase); MCL-16 and MCL-17 as two-thread TransactionTestCases, mutation-checked (remove the MR lock or `lock_booking()` in the close)

Gate A: `.claude/rmo/bin/rmo gate-a --session 1 --log "Session 1 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, GL, IO)

Resume: —

## Session 2 — Reconciler, exclusions, mock, docs

State: pending
Covers: MCL-8, MCL-9, MCL-11, MCL-12, MCL-13, MCL-14, MCL-15, MCL-18

- [ ] T1 — `reviews/reconciler.py` `_reconcile_closures`, and `_reconcile_merge_request` queuing `confirm_reopen` for a listed closed MR — MCL-8, MCL-9
- [ ] T2 — watchdog `_no_progress` excludes `closed`; the panel "Closed" label — MCL-11, MCL-15
- [ ] T3 — regression tests: MCL-11 (watchdog), MCL-12 (rebooking), MCL-13 (merge retry), MCL-14 (urgency check), MCL-15 (panel My MRs, My reviews, My merges)
- [ ] T4 — mock `close` and `reopen` commands in `gitlab_mock/app.py` and `scripts/gitlab-mock`, with a smoke test — MCL-18
- [ ] T5 — docs: README, `docs/testing.md` scenario S, and INVARIANTS.md (GL-6, DATA-6, next letter T)

Gate A: `.claude/rmo/bin/rmo gate-a --session 2 --log "Session 2 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families GL, IO, AUTH)

Resume: —
