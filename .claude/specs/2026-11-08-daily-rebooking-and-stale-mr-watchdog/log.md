# LOG: Daily rebooking and stale-MR watchdog

<!-- Append-only, newest last. Write entries with `rmo log "<heading>" "<line>"`:
     one per approval, gate result, review round, adjudication, spec delta. -->

## 2026-11-08 — spec created
Route: project · Key: RBW

## 2026-11-08 11:49 UTC — spec approved
sha256 b51325e3c263

## 2026-11-08 12:01 UTC — Session 1 Gate A
tests FAIL (1 failed, 101 passed, 292 subtests passed in 37.80s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (3/3)
Gate A: red

## 2026-11-08 12:02 UTC — Session 1 Gate A
tests FAIL (1 failed, 101 passed, 292 subtests passed in 37.59s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (3/3)
Gate A: red

## 2026-11-08 12:11 UTC — Session 1 Gate A (after review fixes)
tests FAIL (1 failed, 101 passed, 292 subtests passed in 36.83s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (3/3)
Gate A: red

## 2026-11-08 12:11 UTC — Session 1 Gate B round 1
rmo-reviewer (spec, style, query): APPROVE, 2 Low. rmo-deep-reviewer (logic, security; DATA, TX): COMMENT, 0 Critical/High, 2 Medium, 2 Low.
All confirmed and fixed: M1 db_default=Now() on unbooked_since (old-worker inserts); M2 settings admins save only editable fields so a day claim is never overwritten (also fixes MergeRoutingSettings), test added to RBW-22 and mutation-checked; L1 _loaded_state moves only when state is written or refreshed; L2 migration adds the MergeRequest field before AreaReview (LOCK-1); settings row created in 0013 as 0010 does.
No re-review: no Critical or High. Gate A after fixes: all green except the pre-existing gitlab_mock smoke test (fails on main).

## 2026-11-08 13:02 UTC — Session 1 Gate A (after smoke-test fix)
tests PASS (102 passed, 292 subtests passed in 36.73s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (3/3)
Gate A: green

## 2026-11-08 13:02 UTC — Session 1 done
Gate A green after the gitlab_mock smoke-test fix (102 passed); Gate B green (round 1, no open Critical/High).

## 2026-11-08 13:07 UTC — Session 2 Gate A
tests PASS (113 passed, 305 subtests passed in 39.61s) · lint FAIL (Your code has been rated at 9.99/10 (previous run: 10.00/10, -0.01)) · imports PASS (clean) · format FAIL (1 file would be reformatted, 117 files would be left unchanged.) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-08 13:08 UTC — Session 2 Gate A
tests PASS (113 passed, 305 subtests passed in 38.66s) · lint FAIL (Your code has been rated at 9.99/10 (previous run: 9.99/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-08 13:09 UTC — Session 2 Gate A
tests PASS (113 passed, 305 subtests passed in 39.35s) · lint FAIL (Your code has been rated at 9.99/10 (previous run: 9.99/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-08 13:11 UTC — Session 2 Gate A
tests PASS (113 passed, 305 subtests passed in 39.49s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: green

## 2026-11-08 13:50 UTC — spec delta (developer approved)
Add RBW-23: skip an awaiting_booking MR while its resolve_areas follow-up job is pending or in_flight (Session 2 deep review M1: the RBW-7 transition could take the MR out of awaiting_booking first, and the follow-up then exits). Clarify RBW-11: a selected MR with nothing to book counts as skipped; the admin-run summary lists only the selected MRs' leftovers (matches the code).

## 2026-11-08 13:50 UTC — spec approved
sha256 96c96088b726
delta: add RBW-23, clarify RBW-11

## 2026-11-08 13:53 UTC — Session 2 Gate A (after review round 1)
tests PASS (114 passed, 308 subtests passed in 39.05s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: red

## 2026-11-08 13:54 UTC — Session 2 Gate A (after review round 1)
tests PASS (114 passed, 308 subtests passed in 39.38s) · lint FAIL (Your code has been rated at 9.99/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: red

## 2026-11-08 13:56 UTC — Session 2 Gate A (after review round 1)
tests PASS (114 passed, 308 subtests passed in 38.55s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: green

## 2026-11-08 14:00 UTC — Session 2 Gate A (after delta re-review)
tests PASS (114 passed, 308 subtests passed in 38.15s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: green

## 2026-11-08 14:00 UTC — Session 2 Gate B
Round 1: rmo-reviewer COMMENT (4 Low); rmo-deep-reviewer COMMENT (1 Medium M1, 3 Low). All confirmed. M1 fixed by spec delta RBW-23; Lows fixed (shared mark_in_review helper, OrderBy annotation, audit + summary in one atomic block, post-lock state read, RBW-9 rollback test).
Delta re-review (deep-reviewer): round-1 findings all fixed; new Medium N1 (RBW-23 lock-time check untested because a selection-time filter hid it). Fixed by removing the selection-time filter so every MR goes through the check under the MR lock; mutation-checked (removing the lock-time check fails the RBW-23 test).
Accepted: a reviewer who asked for more time can be rebooked on the same day by the retry (RBW-4 does not require after_day); the admin summary lists only selected MRs (RBW-11).
Gate B green: no open Critical or High. Session 2 done.
