# LOG: Merge routing

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-11-04 — spec created
Route: project · Key: MRG

## 2026-11-04 08:37 UTC — spec approved
sha256 fed3d2cb53e3

## 2026-11-04 — Session 1 Gate A
tests `scripts/test.sh -q` PASS on two full runs after the TIME_ZONE change (80 passed, 246 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 1` PASS (5/5) · makemigrations --check clean.

## 2026-11-04 — Session 1 Gate B
deep-reviewer: APPROVE, no Critical/High/Medium (probes: past windows, midnight, horizon, working days, leave; INV-1 under the booking lock; INV-2 exclusion; no naive dates after TIME_ZONE). reviewer: COMMENT, one Medium (stale design row) fixed. Lows fixed: ranking/horizon/working-day subTests; HH:MM validator moved to the field; typing; docstrings.
Accepted: no composite index on (merge_owner, window) yet (small table); every approval alerts the admin while no merge owners are configured; an MR left unassigned (MRG-2) is not offered again — raise with the developer.
Session 1: done.
