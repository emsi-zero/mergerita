# LOG: Urgency gaps: opted-out merge owners, expiry, reminders, working days

<!-- Append-only, newest last. Write entries with `rmo log "<heading>" "<line>"`:
     one per approval, gate result, review round, adjudication, spec delta. -->

## 2026-11-11 — spec created
Route: project · Key: UGF

## 2026-11-11 09:25 UTC — spec approved
sha256 13f806fe4131

## 2026-11-11 09:29 UTC — Session 1 Gate A
tests PASS (159 passed, 412 subtests passed in 52.50s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (4/4)
Gate A: green

## 2026-11-11 09:33 UTC — Session 1 Gate B
rmo-reviewer APPROVE (1 Low: UGF-2 admin-recipient branch untested). rmo-deep-reviewer COMMENT (2 Low: no two-thread approve-vs-merge test; no midnight probe for the working-day check). No Critical/High/Medium.
Every merge interleaving holds: confirm_merge is the only path to merged and holds the MR lock; approve/decline/withdraw/request re-read under the same lock, so a request is never approved after the merge nor expired mid-approval.
Decisions: the admin-recipient subTest (UGF-2) and the midnight probe (UGF-3) are added in Session 2, which edits the same test files. The approve-vs-merge race test is accepted as covered by the URG-12 and URG-26 lock tests.
Gate A green: 159 passed, trace 4/4. Session 1 done.

## 2026-11-11 09:41 UTC — Session 2 Gate A
tests PASS (164 passed, 427 subtests passed in 57.94s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports FAIL (ERROR: /home/tabdeal/side-projects/merge-orchestrator/.claude/worktrees/urgency-gaps/reviews/tests/test_urgency.py Imports are incorrectly sorted and/or formatted.) · format PASS (clean) · migrations PASS (clean) · trace PASS (5/5)
Gate A: red

## 2026-11-11 09:42 UTC — Session 2 Gate A
tests PASS (164 passed, 427 subtests passed in 58.38s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (5/5)
Gate A: red

## 2026-11-11 09:43 UTC — Session 2 Gate A
tests PASS (164 passed, 427 subtests passed in 58.25s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (5/5)
Gate A: green

## 2026-11-11 09:45 UTC — Session 2 Gate A
tests PASS (164 passed, 427 subtests passed in 57.56s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (5/5)
Gate A: green

## 2026-11-11 09:52 UTC — Session 2 Gate A (after review round 1)
tests PASS (164 passed, 429 subtests passed in 57.65s) · lint FAIL (Your code has been rated at 10.00/10 (previous run: 10.00/10, -0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (5/5)
Gate A: red

## 2026-11-11 09:54 UTC — Session 2 Gate A (after review round 1)
tests PASS (164 passed, 429 subtests passed in 57.50s) · lint PASS (Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)) · imports PASS (clean) · format PASS (clean) · migrations PASS (clean) · trace PASS (5/5)
Gate A: green

## 2026-11-11 09:56 UTC — Session 2 Gate B
rmo-reviewer APPROVE (2 Low below threshold). rmo-deep-reviewer COMMENT (1 Medium: per-request failure isolation untested; 3 Low: a request failing every pass was only logged, the worker wiring untested and idle_loop ran the real check, the walk stops at a visited delegate).
Fixed: failure-isolation subTest in UGF-4; run_urgency_check now raises once at the end naming the failed requests so run_guarded alerts once per streak; idle_loop mocks run_urgency_check and a UGF-6 subTest asserts the worker calls it through run_guarded. README admin page name corrected.
Accepted: escalation from a delegate who reports to the requester goes to the admins (fails safe); every pass re-locks pending requests past the reminder time, bounded by the pending set.
No re-review (no Critical or High). Gate A green: 164 passed, trace 9/9. Session 2 done.

## 2026-11-11 09:57 UTC — done
Opted-out merge owners never get urgent merges (UGF-1); pending requests expire on merge (UGF-2); days off count in approver resolution (UGF-3); reminders and escalation for unanswered requests on their own cadence, with a watchdog case (UGF-4..8); admin-validated thresholds (UGF-9). 9/9 criteria traced; both gates green in both sessions.

## 2026-11-11 09:57 UTC — spec closed
Session archived: .claude/rmo/state/archive/session-20261111T095701Z.json
