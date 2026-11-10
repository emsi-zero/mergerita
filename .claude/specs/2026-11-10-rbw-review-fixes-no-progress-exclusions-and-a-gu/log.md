# LOG: RBW review fixes: No progress exclusions and a guarded worker loop

<!-- Append-only, newest last. Write entries with `rmo log "<heading>" "<line>"`:
     one per approval, gate result, review round, adjudication, spec delta. -->

## 2026-11-10 — spec created
Route: task · Key: RWF

## 2026-11-10 11:43 UTC — spec approved
sha256 db54aea7b930

## 2026-11-10 11:46 UTC — Gate A
tests PASS (128 passed, 317 subtests passed in 36.98s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format FAIL (1 file would be reformatted, 120 files would be left unchanged.) · migrations PASS (clean) · trace PASS (6/6)
Gate A: red

## 2026-11-10 11:47 UTC — Gate A (after lint and format)
tests PASS (128 passed, 317 subtests passed in 36.70s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (6/6)
Gate A: green

## 2026-11-10 11:53 UTC — Gate A (after review round 1)
tests PASS (128 passed, 317 subtests passed in 36.63s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (6/6)
Gate A: green

## 2026-11-10 11:54 UTC — Gate B round 1
rmo-reviewer APPROVE (1 Low: guard as a class). rmo-deep-reviewer COMMENT (0 Critical/High/Medium; 3 Low, 2 below threshold).
All Lows fixed: the guard is a function run_guarded with an explicit failing set (pylint disable gone); a dead connection is closed after a failure so Django reconnects (narrow is_usable() check, not close_if_unusable_or_obsolete, which also closes on an autocommit mismatch); a streak is marked alerted only after the alert committed; later failures of a streak log one line, not a traceback; README notes the db has no restart policy.
Correction to the spec's RWF-5 rationale: reconcile_all already catches GitLab errors per project and only logs them, so the guard alerts on database-level failures, not on a GitLab outage. One alert per streak still stands.
Accepted: a future pending session on one area review keeps the MR out of No progress even when another area review is stuck without a session (RWF-1 as worded).
No re-review (no Critical or High). Gate A green: 128 passed, trace 6/6.
