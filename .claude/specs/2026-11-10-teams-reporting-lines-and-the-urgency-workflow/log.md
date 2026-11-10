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
