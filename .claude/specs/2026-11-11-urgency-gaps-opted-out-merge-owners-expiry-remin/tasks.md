# TASKS: Urgency gaps: opted-out merge owners, expiry, reminders, working days

Spec: spec.md · Key: UGF · Route: project · State: pending
Checkpoint: `.claude/rmo/bin/rmo gate-a`

## Session 1 — Routing, expiry, working days, and the migration

State: done
Covers: UGF-1, UGF-2, UGF-3, UGF-9

- [x] T1 — migration `reviews/0015_urgency_gaps.py` and model changes (`expired`, `approver_since`, `reminded_at`, the two thresholds with validators and `RebookingSettings.clean()`) — UGF-9
- [x] T2 — `merge_router._best_fit`: an effective limit of 0 is never a candidate — UGF-1
- [x] T3 — `urgency.expire_pending`, called from `merges.record_merge` — UGF-2
- [x] T4 — `urgency._available` checks working days, and the `_walk` refactor (escalation uses it in Session 2) — UGF-3
- [x] T5 — tests: UGF-1 in `reviews/tests/test_urgent_booking.py`, UGF-2 and UGF-3 in `reviews/tests/test_urgency.py`, UGF-9 in `reviews/tests/test_rebooking_data.py` or a new settings test

Gate A: `.claude/rmo/bin/rmo gate-a --session 1 --log "Session 1 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, SEP)

Resume: Session 1 code and tests done; Gate A run. Next: Session 2 T1 (urgency.working_hours, run_urgency_check).

## Session 2 — Reminders, escalation, watchdog case, docs

State: pending
Covers: UGF-4, UGF-5, UGF-6, UGF-7, UGF-8

- [ ] T1 — `urgency.working_hours`, `urgency.run_urgency_check` (remind, escalate, per-request transaction) — UGF-4, UGF-5
- [ ] T2 — `RMO_URGENCY_CHECK_INTERVAL_SECONDS` and the worker loop call through `run_guarded` — UGF-6
- [ ] T3 — watchdog "Urgency pending" case with a `Finding.detail` line — UGF-8
- [ ] T4 — tests: UGF-4, 5, 8 (TestCase); UGF-6 and UGF-7 as two-thread TransactionTestCases (mutation-checked: no MR lock in the check, no re-read)
- [ ] T5 — docs: README "Teams and urgency", `docs/testing.md` scenario R steps

Gate A: `.claude/rmo/bin/rmo gate-a --session 2 --log "Session 2 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, IO, AUTH, DATA)

Resume: —
