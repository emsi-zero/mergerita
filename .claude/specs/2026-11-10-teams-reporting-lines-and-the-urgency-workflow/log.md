# LOG: Teams, reporting lines and the urgency workflow

<!-- Append-only, newest last. Write entries with `rmo log "<heading>" "<line>"`:
     one per approval, gate result, review round, adjudication, spec delta. -->

## 2026-11-10 — spec created
Route: project · Key: URG

## 2026-11-10 12:07 UTC — spec approved
sha256 c40c38a051bb

## 2026-11-10 12:12 UTC — Session 1 Gate A
tests PASS (132 passed, 318 subtests passed in 39.73s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (4/4)
Gate A: green

## 2026-11-10 12:17 UTC — Session 1 Gate A (after review round 1)
tests PASS (132 passed, 321 subtests passed in 40.88s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (4/4)
Gate A: green

## 2026-11-10 12:18 UTC — Session 1 Gate B
rmo-reviewer APPROVE (2 Low: Team.name unique not in Contracts; TeamAdmin list N+1). rmo-deep-reviewer COMMENT (0 Critical/High, 2 Medium).
M1 confirmed and fixed: deleting a Team or a manager left dangling DO_NOTHING ids that made the panel header raise DoesNotExist (500 on every panel page). Person.team, Person.reports_to and Team.delegate are now SET_NULL, Team.lead PROTECT (Django-side; migration 0014 edited in place, not yet applied anywhere). Tests: URG-4 subTest (header renders after team and manager are deleted, mutation-checked), URG-1 subTest (a team lead cannot be deleted).
M2 confirmed and fixed: URG-2 subTest for a loop higher up the chain (the save ends and is accepted).
Lows: list_select_related on TeamAdmin; Team.name unique kept and recorded in design.md (a data rule, not behaviour).
No re-review (no Critical or High). Gate A green: 132 passed, trace 4/4. Session 1 done.

## 2026-11-10 12:24 UTC — Session 2 Gate A
tests PASS (142 passed, 355 subtests passed in 49.36s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format FAIL (1 file would be reformatted, 125 files would be left unchanged.) · migrations PASS (clean) · trace PASS (9/9, 1 with >1 test)
Gate A: red

## 2026-11-10 12:26 UTC — Session 2 Gate A
tests PASS (142 passed, 355 subtests passed in 49.10s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (9/9, 1 with >1 test)
Gate A: green

## 2026-11-10 12:37 UTC — spec delta (developer approved)
Session 2 review: URG-10 and URG-12 contradicted each other (404 vs a message for a request that is no longer pending). URG-10 now covers only a person who may not decide; URG-12's loser (decider or withdrawing author) gets a redirect with "This request is no longer pending.".
Added URG-26: approving a request whose MR is no longer open (e.g. merged) is refused with "This MR is no longer open."; declining stays allowed (deep review M4: approval set urgent_at on a merged MR and Session 3 would book it).

## 2026-11-10 12:37 UTC — spec approved
sha256 550211bb36aa
delta: URG-10 and URG-12 reconciled, URG-26 added

## 2026-11-10 12:41 UTC — Session 2 Gate A (after review round 1)
tests PASS (143 passed, 365 subtests passed in 48.87s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (10/10, 1 with >1 test)
Gate A: red

## 2026-11-10 12:43 UTC — Session 2 Gate A (after review round 1)
tests PASS (143 passed, 365 subtests passed in 49.59s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (10/10, 1 with >1 test)
Gate A: red

## 2026-11-10 12:44 UTC — Session 2 Gate A (after review round 1)
tests PASS (142 passed, 364 subtests passed in 48.12s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (10/10)
Gate A: green

## 2026-11-10 12:45 UTC — Session 2 Gate A (after review round 1)
tests PASS (142 passed, 364 subtests passed in 48.30s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (10/10)
Gate A: green

## 2026-11-10 12:49 UTC — Session 2 Gate B
Round 1: rmo-reviewer COMMENT (3 Medium: URG-10/URG-12 conflict, refusal flash in success style, weekly_count N+1 on the tab); rmo-deep-reviewer COMMENT (4 Medium: same conflict, AUTH-5 tests missing for request/withdraw, URG-12 race test not running the real withdraw, approve on a merged MR; 2 Low + 2 below threshold). All confirmed. Spec delta approved (URG-10, URG-12 reconciled; URG-26 added).
Fixes: redirect + message for a lost race, 404 only without rights; URG-26 check in approve; deterministic URG-12 test running the real withdraw/approve/decline (mutation-checked); one URG-5 test; notice-err style; team_status once per team; AUTH-5 subTests; request() re-reads state after the lock; weekly window midnight probe; missing approver falls back to admins.
Accepted: two approvals for one team at the same instant can miss the over-limit alert (warn only).
Delta re-review (deep-reviewer): APPROVE, all seven fixed, no new finding. Gate A green on three runs (142 passed, trace 10/10). Session 2 done.
