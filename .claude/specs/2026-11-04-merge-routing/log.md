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

## 2026-11-04 — Session 2 Gate B round 1
reviewer: COMMENT — Mediums: migration 0011 not in Contracts; null-owner merge row with by_other_person=True unspecified; MRG-8 panel labels untested. Lows: cancel text and precedence not in Contracts; e2e test names no criterion; docstrings.
deep-reviewer: REQUEST_CHANGES — High: null merged_by (auto-merge) records a clean merge with no alert; merge_user ignored. Mediums: revoke-then-merge classified from stale approvals (INV-3); a non-GitLabError on one MR drops all reconciled merges of the project; merge hook trusted for terminal state and merger. Lows: null `user` in hook quarantines the event; MRG-11 re-assignment untested.
Adjudication: all confirmed.

## 2026-11-04 — spec delta (developer approved)
Developer decisions: confirm every merge via GitLab in an outbox job `confirm_merge` (hook and reconcile are triggers only); merge_user then merged_by, and a merge with no merger alerts admins (new MRG-12); by_other_person false when no owner was assigned; Contracts gain migration 0011, violation precedence author > approver > before_approval, and the cancel chat text. MRG-5, MRG-7, MRG-8, INV-3, INV-4, Contracts, Validation and design.md updated; task T7 added.

## 2026-11-04 09:36 UTC — spec delta approved
sha256 b4dd4c56074a

## 2026-11-04 — follow-up decided
Developer: the MRG-2 gap (an unassigned approved MR is never offered again) is the next spec: a daily retry of merge assignment.

## 2026-11-04 — Session 2 Gate A (after round 1 fixes)
tests `scripts/test.sh -q` PASS (89 passed, 265 subtests) · pylint 10.00 PASS · isort PASS · black PASS · makemigrations --check clean · `craft-spec trace` PASS (12/12). The e2e docstring no longer names MRG-6 (trace allows one test per criterion; the e2e is an extra integration check).
Re-review of the delta: deep-reviewer (logic, security), because round 1 confirmed a High.

## 2026-11-04 — Session 2 Gate B round 2 (delta re-review)
deep-reviewer: COMMENT — all six round-1 findings fixed; INV-4 and INV-5 hold under concurrent and retried confirm_merge jobs; a forged hook only triggers a GitLab read. No Critical/High.
Mediums (fixing, Gate A only): a FAILED confirm_merge from reconcile is never re-queued (stable inbox key); no gateway test pins merge_user before merged_by or the malformed-body contract. Low fixing: non-JSON body raises JSONDecodeError, not GitLabError.
Accepted: an approval landing between the verdict read and the MR lock gives a false before_approval alert (fails closed); freshness is judged against the DB's latest push, so a lost push hook followed by an immediate merge counts a stale approval as fresh (needs a lost webhook; revisit if seen).

## 2026-11-04 — Session 2 Gate A (after round 2 fixes)
tests `scripts/test.sh -q` PASS (90 passed, 271 subtests) · pylint 10.00 PASS · isort PASS · black PASS · makemigrations --check clean · `craft-spec trace` PASS (12/12, no warnings). Round 2 Mediums and Low fixed; no re-review (no High).
Gate B: green. Session 2: done.

## 2026-11-04 09:52 UTC — spec closed
Session archived: /home/tabdeal/side-projects/merge-orchestrator/.claude/craft/archive/session-20261104T095257+0000.json
