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
