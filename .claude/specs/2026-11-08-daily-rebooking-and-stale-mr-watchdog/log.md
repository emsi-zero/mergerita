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
