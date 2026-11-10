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

## 2026-11-10 12:56 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 49.51s) · lint FAIL (Your code has been rated at 9.99/10 (previous run: 10.00/10, -0.01)) · imports PASS (clean) · format FAIL (3 files would be reformatted, 124 files would be left unchanged.) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-10 12:57 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 49.65s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 9.99/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-10 12:59 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 50.64s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-10 13:00 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 49.89s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-10 13:02 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 51.15s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: red

## 2026-11-10 13:04 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 50.85s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: green

## 2026-11-10 13:05 UTC — Session 3 Gate A
tests PASS (153 passed, 376 subtests passed in 51.51s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (11/11)
Gate A: green

## 2026-11-10 13:41 UTC — spec delta (developer approved)
Session 3 review: URG-14 now moves pending sessions on any day other than today (later or overdue); URG-15 applies continuity only within the earliest day any eligible owner works, never moving the booking later (deep review H1); URG-18 sends one alert per approval listing the area reviews, one per later urgent booking that lands after today or does not fit, and none from the daily rebooking (RBW-10); added URG-27: with a pending or in-flight resolve_areas follow-up the approval only marks the MR urgent (the follow-up books urgently), otherwise it moves an awaiting_booking MR with no unbooked area review to in_review (deep review H2, the RBW-23 class).

## 2026-11-10 13:41 UTC — spec approved
sha256 c88c80f6cc24
delta: URG-14, 15, 18 changed, URG-27 added

## 2026-11-10 13:46 UTC — Session 3 Gate A (after review round 1)
tests PASS (154 passed, 385 subtests passed in 62.93s (0:01:02)) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: red

## 2026-11-10 13:48 UTC — Session 3 Gate A (after review round 1)
tests PASS (154 passed, 385 subtests passed in 60.34s (0:01:00)) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: green

## 2026-11-10 13:49 UTC — Session 3 Gate A (after review round 1)
tests PASS (154 passed, 385 subtests passed in 55.76s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: green

## 2026-11-10 13:55 UTC — Session 3 Gate A (after delta re-review fix)
tests PASS (154 passed, 386 subtests passed in 61.67s (0:01:01)) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (12/12)
Gate A: green

## 2026-11-10 13:56 UTC — Session 3 Gate B
Round 1: rmo-reviewer COMMENT (3 Medium); rmo-deep-reviewer REQUEST_CHANGES (2 High: continuity booked a later day silently, URG-14/18; approval moved an awaiting_booking MR to in_review while resolve_areas was pending, leaving an area review with no session, the RBW-23 class; 3 Medium: overdue sessions kept, urgent alert ignored alert_on_no_fit, one alert per area review; 3 Low). All confirmed. Spec delta approved (URG-14, 15, 18 changed; URG-27 added).
Fixes: _pick_slot prefer= within the earliest day; URG-27 guard; overdue sessions moved; one alert per approval, none from the rebooking; URG-25 in both orders; urgent reason text; overage boundary subTests.
Delta re-review: all fixed; one new High: urgent book_follow_up could rebook the reviewer who handed back (SEP-1). Fixed: book_follow_up excludes every handed-back reviewer in both branches; URG-19 subTest, mutation-checked. Re-review: APPROVE.
Accepted: the need-more-time urgent alert says 'no owner is available today' (wording only). Follow-up raised to the developer, outside this spec: outcomes.hand_back excludes only the current reviewer, not earlier hand-backs.
Gate A green: 154 passed, trace 12/12. Session 3 done.

## 2026-11-10 14:00 UTC — Session 4 Gate A
tests PASS (155 passed, 391 subtests passed in 73.61s (0:01:13)) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format FAIL (1 file would be reformatted, 126 files would be left unchanged.) · migrations PASS (clean) · trace PASS (1/1)
Gate A: red

## 2026-11-10 14:02 UTC — Session 4 Gate A
tests PASS (155 passed, 391 subtests passed in 71.64s (0:01:11)) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (1/1)
Gate A: green

## 2026-11-10 14:09 UTC — Session 4 Gate A (after review round 1)
tests FAIL (1 failed, 154 passed, 396 subtests passed in 65.09s (0:01:05)) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (27/27)
Gate A: red

## 2026-11-10 14:11 UTC — Session 4 Gate A (after review round 1)
tests PASS (155 passed, 397 subtests passed in 71.43s (0:01:11)) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (27/27)
Gate A: green

## 2026-11-10 14:14 UTC — Session 4 Gate B
rmo-reviewer COMMENT (Lows: scenario R wrong tab and no decline step; README missing the reason-length setting and the 7-day definition; urgent signature differs from Contracts). rmo-deep-reviewer COMMENT (2 Medium: a missed past-day merge assignment was kept, URG-23 says from now; test gaps; 3 Low).
Fixed: keep only an open assignment dated today, cancel and re-route later and missed ones (mutation-checked); URG-23 subTests for today stays, yesterday missed, midnight probes, merge_retry derived flag, a normal MR after an over-limit day; scenario R and README fixed; test date built from TUESDAY. No re-review (no Critical or High).
Recorded: Contracts say urgent: bool = False for book_area_review and assign_merge; the code uses urgent: bool | None = None (None derives from urgent_at), as design.md states.
Accepted: a cancel followed by no fit leaves the MR to the daily merge retry; a re-route can return the same owner and window (two chat messages).
Observed once: tests_e2e test_approved_merge_request_gets_reviewers_in_the_mock failed with a GitLabError transport error from the mock; passed on 6 later runs, no gateway or mock change in this spec.
Gate A green: 155 passed, trace 27/27. Session 4 done.

## 2026-11-10 14:14 UTC — done
Teams and reporting lines (URG-1..4); urgency requests, approver resolution and decisions (URG-5..13, 26); urgent booking over budget (URG-14..22, 24, 25, 27); urgent merge routing over the daily limit (URG-23). 27/27 criteria traced; both gates green in all four sessions.

## 2026-11-10 14:14 UTC — spec closed
Session archived: .claude/rmo/state/archive/session-20261110T141450Z.json
