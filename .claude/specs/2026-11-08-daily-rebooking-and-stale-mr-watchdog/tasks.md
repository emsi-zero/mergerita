# TASKS: Daily rebooking and stale-MR watchdog

Spec: spec.md · Key: RBW · Route: project · State: pending
Checkpoint: `.claude/rmo/bin/rmo gate-a`

## Session 1 — Data: state_changed_at, unbooked_since, settings row, migration

State: pending
Covers: RBW-20, RBW-21, RBW-22

- [ ] T1 — `MergeRequest.state_changed_at`, plus `from_db` and a `save()` override that stamps it when the state changes and adds it to `update_fields`, in `reviews/models.py` — RBW-20
- [ ] T2 — `AreaReview.unbooked_since` (default `timezone.now`, nullable). `_unbook()` sets it and `_book()` clears it, in `reviews/booking.py` — RBW-21 (data), used by RBW-2
- [ ] T3 — the `RebookingSettings` model with `load()` and validators, and its admin (no add or delete), in `reviews/models.py` and `reviews/admin.py` — RBW-22
- [ ] T4 — migration `reviews/migrations/0013_rebooking_and_watchdog.py`, with a module-level `backfill` — RBW-21
- [ ] T5 — tests in `reviews/tests/test_rebooking_data.py`: RBW-20 (a state change, a non-state save, create), RBW-21 (call `backfill` with the live registry: an MR with audit, an MR without audit, an unbooked area review with and without an `area_review.unbooked` event), RBW-22 (the admin form rejects 0 and 61 and accepts 1 and 60)
- [ ] T6 — run the existing suite: no test breaks from the new `update_fields` behaviour

Gate A: `.claude/rmo/bin/rmo gate-a --session 1 --log "Session 1 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families DATA, TX)

Resume: —

## Session 2 — Daily rebooking, admin action, worker wiring

State: pending
Covers: RBW-1, RBW-2, RBW-3, RBW-4, RBW-5, RBW-6, RBW-7, RBW-8, RBW-9, RBW-10, RBW-11

- [ ] T1 — `alert_on_no_fit: bool = True` in `booking.book_area_review`, in `reviews/booking.py` — RBW-10
- [ ] T2 — `reviews/rebooking.py`: `run_daily_rebooking`, `retry_unbooked`, `_rebook_one`, `_summary`, following design.md "Write paths and locks" — RBW-1, 2, 4, 5, 6, 7, 9, 10
- [ ] T3 — call `run_daily_rebooking()` in `reviews/management/commands/run_worker.py` — RBW-1
- [ ] T4 — the "Retry booking" action on `MergeRequestAdmin`, in `reviews/admin.py` — RBW-11
- [ ] T5 — tests in `reviews/tests/test_rebooking.py`: RBW-1, 2, 4, 5, 6, 7, 9, 10, 11 (TestCase); RBW-3 and RBW-8 (two-thread TransactionTestCase, checked by removing the claim lock and the MR re-check)

Gate A: `.claude/rmo/bin/rmo gate-a --session 2 --log "Session 2 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, IO, SEP)

Resume: —

## Session 3 — Stale-MR watchdog, worker wiring, docs

State: pending
Covers: RBW-12, RBW-13, RBW-14, RBW-15, RBW-16, RBW-17, RBW-18, RBW-19

- [ ] T1 — `reviews/watchdog.py`: `run_daily_watchdog`, `working_days`, the case finders as a `(label, finder)` list, `_summary`, and the missed-slot chat — RBW-12 to RBW-19
- [ ] T2 — call `run_daily_watchdog()` after `run_daily_rebooking()` in `run_worker.py` — RBW-12
- [ ] T3 — tests in `reviews/tests/test_watchdog.py`: RBW-13 to RBW-19 (TestCase; RBW-19 has a midnight Asia/Tehran case and a weekend case; RBW-14 asserts that no MR, area review or session row changed); RBW-12 (two-thread TransactionTestCase, checked by removing the lock; plus "not before the rebooking claim")
- [ ] T4 — docs: `docs/testing.md` scenarios P and Q; README section "Rebooking and the watchdog"
- [ ] T5 — `.claude/rmo/INVARIANTS.md`: add "MR state changes only through `MergeRequest.save()`" (DATA-5), and set the next scenario letter to R

Gate A: `.claude/rmo/bin/rmo gate-a --session 3 --log "Session 3 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, IO)

Resume: —
