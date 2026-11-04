# LOG: Merge routing daily retry

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-11-04 — spec created
Route: task · Key: MRDR

## 2026-11-04 10:10 UTC — spec approved
sha256 dcca3e73b6b8

## 2026-11-04 — spec approved
sha256 dcca3e73b6b8 · developer decisions: daily retry at an admin-set time (default 08:00 Asia/Tehran), one daily summary alert, an admin action for a manual retry.

## 2026-11-04 — Gate A
tests `scripts/test.sh -q` PASS (97 passed, 284 subtests) · pylint 10.00 PASS · isort PASS · black PASS · makemigrations --check clean · `craft-spec trace` PASS (6/6).

## 2026-11-04 — Gate B round 1
reviewer: APPROVE — one Low: the once-per-day sequence test carried no criterion ID.
deep-reviewer: REQUEST_CHANGES — High: the eligibility re-check ran in the same statement as the MR lock, so an assignment committed while the retry waited was not seen; the second insert hit merge_assignment_one_open and the IntegrityError stopped the admin action or the daily run and the worker loop. Medium: a crash or an exception after the day claim lost the rest of the run with no trace.
Adjudication: both confirmed. Fixes: lock the MR, then check eligibility in a new statement (`_route_one`); a per-MR exception is logged, the run goes on, and the MR is listed as still unassigned in the summary; the sequence test merged into the MRDR-2 test; MRDR-5 is now a real two-connection test, verified to fail on the old code.
Accepted: a process crash after the day claim still waits until the next day (at-most-once, by design of INV-3).

## 2026-11-04 — Gate A (after round 1 fixes)
tests `scripts/test.sh -q` PASS on two full runs (96 passed, 284 subtests) · pylint 10.00 PASS · isort PASS · black PASS · makemigrations --check clean · `craft-spec trace` PASS (6/6).
Re-review of the delta: deep-reviewer (High confirmed in round 1).

## 2026-11-04 — Gate B round 2 (delta re-review)
deep-reviewer: APPROVE — the High is fixed (new snapshot after the MR lock; every writer takes the MR lock first); the broad except is outside the atomic block and logs every failure; the MRDR-5 test waits on the lock and its assigned case separates old and new code.
Accepted Low: the error handler's MR read can raise only on a lost DB connection; the day is then already claimed and only that run's audit row is lost.
Gate B: green. Done.
