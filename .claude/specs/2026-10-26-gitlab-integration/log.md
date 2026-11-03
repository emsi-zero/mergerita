# LOG: GitLab integration

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-10-26 — spec created
Route: project · Key: GLI

## 2026-10-26 13:24 UTC — spec approved
sha256 a3ef55d1745d

## 2026-10-28 — parked
Approved and planned, not started. Parked so the developer panel can go first (developer decision). Resume: write `2026-10-26-gitlab-integration` into `.claude/specs/ACTIVE`, run `bin/craft-approve` if the gate asks again, then Session 1.

## 2026-10-28 12:29 UTC — spec closed
Session archived: /home/tabdeal/side-projects/merge-orchestrator/.claude/craft/archive/session-20261028T122920+0000.json

## 2026-11-02 — resumed, spec delta: mock GitLab service
Resumed from parked. Added GLI-12 (end-to-end booking through the mock) and GLI-13 (reconciliation-only end to end); non-goals and contracts name the dev-only `gitlab_mock` FastAPI service and its CLI. Plan: new Session 1 builds the mock; old Sessions 1–3 become 2–4; GLI-12 lands in Session 3, GLI-13 in Session 4.
Why: the developer cannot configure the work GitLab (not a maintainer) and wants integration tests and a manual demo against a mock service. Approved by the developer.

## 2026-11-02 09:42 UTC — spec delta approved
sha256 44af8411b748

## 2026-11-02 — spec delta: mock `assign`
Contracts: the mock CLI and control API gain `open --reviewer` and `assign` (adds a reviewer to an open MR and sends GitLab's reviewer-change Merge Request Hook). Why: GLI-12 needs the AI Reviewer added the way authors do at work; Session 1 reviewer flagged it as outside the contract. Approved by the developer.

## 2026-11-02 10:28 UTC — spec delta approved
sha256 d6bc8fac914e

## 2026-11-02 — Session 1 (mock GitLab) Gate A
tests `scripts/test.sh -q` PASS (36 passed, 60 subtests; 4 mock smoke tests) · pylint 10.00 PASS · isort PASS · black PASS. Trace: no criterion in this session (scaffolding); GLI-1..13 land in Sessions 2–4.

## 2026-11-02 — Session 1 Gate B
reviewer (spec, style): APPROVE. Medium (`assign` outside the contract) → spec delta approved. Fixed Lows: PUT `title` branch dropped; `unapproved` state added; CLI `--rename` and `--too-large`; typing; docstrings.
Session 1: done.

## 2026-11-02 — spec delta: per-call deadline
Contracts: new setting `RMO_GITLAB_CALL_DEADLINE_SECONDS` (45). Why: Session 2 deep-review Medium — the 10 s httpx timeout bounds one phase of one request, so a many-page call could outlive the 60 s outbox lease and be re-run by another worker. Approved by the developer.

## 2026-11-02 10:47 UTC — spec delta approved
sha256 9647bfdbd6fd

## 2026-11-02 — Session 2 (gateway) Gate A
tests `scripts/test.sh -q` PASS (41 passed, 68 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 2` PASS (4/4)

## 2026-11-02 — Session 2 Gate B
reviewer (spec, style): COMMENT, Medium (gateway too_large/collapsed mapping untested) → fixed. deep-reviewer (logic, security): REQUEST_CHANGES.
[High, confirmed] set_reviewer GET-then-PUT lost a reviewer when two workers ran calls for one MR → per-MR session-level advisory lock in the handler; two-thread test against the mock fails without the lock (verified by deep-reviewer). Delta re-review: High closed.
Re-review Mediums fixed (no further re-review needed, Gate A only): blocking lock wait spent the lease → `pg_try_advisory_lock` + retryable `ReviewerLockBusy`; per-request timeout capped by the time left before the deadline.
Other fixes: pagination guard (next page must advance); per-call deadline `RMO_GITLAB_CALL_DEADLINE_SECONDS` (spec delta approved); `split("\n")`; https-only `GITLAB_URL` unless DEBUG; docstrings.
Deferred: MR-level diff overflow (`changes_count` "1000+") — check on the work GitLab in the smoke test (Session 4 T6). Fake's single page for `list_open_merge_requests` — Session 4 seeds next_page=None or extends the fake. PgBouncer transaction pooling breaks session advisory locks — documented at the lock.
Session 2: done.

## 2026-11-02 — Session 3 Gate B round 1 + spec delta: fresh AI verdicts
reviewer (spec, style, query): COMMENT, Mediums (no repeated-event test; manual scenarios not marked unverified). deep-reviewer (logic, security): REQUEST_CHANGES.
[High, confirmed, reproduced] After a push, the queued check read the bot's old `requested_changes`, sent the MR back to `waiting_on_author`, and the bot's later approval was ignored — the MR stuck. [Medium] A stale `approved` could pass newer commits.
Spec delta (approved by the developer, option "fresh verdicts only"): GLI-6 acts only when the bot's latest MR comment (read from GitLab) is newer than `MergeRequest.ai_gate_entered_at`; GLI-7 resets that time on every push in `waiting_on_author` or `awaiting_ai_review`; new GLI-14 regression for the reproduced sequence. Freshness read from the API, not webhooks, so reconciliation (GLI-13) still works with lost webhooks. Assumption: the bot comments on every review (to verify in the smoke test).
Manual check by the main session: scenario B ran end to end against the mock over real HTTP (bot + 21 + 22 set in the mock). The docs' `gm` helper clashes with a zsh alias → rename to `gmock`.

## 2026-11-02 12:54 UTC — spec delta approved
sha256 ba5ee064f32b

## 2026-11-02 — Session 3 delta re-review + spec delta: verdict-event freshness
deep-reviewer delta re-review: REQUEST_CHANGES. The first High (stale verdict after a push) is closed for the reproduced order, but [High, confirmed, reproduced] a fresh bot comment combined with the bot's not-yet-updated state re-applied the old `requested_changes`, and the later approval was lost again. [Medium] `_reopen_ai_gate` writes without a row lock and can overwrite ai_gate's move. [Medium] migration 0005 has no DB default (mixed-version deploys). [Medium, Session 4] reconcile has no `oldrev`, so a lost push never reopens the gate. [Low] whole-second webhook times; entry time can move backwards.
Spec delta (approved by the developer, "both"): GLI-6 freshness comes from the bot's verdict system note, read before the state; a fresh `approved` also moves an MR out of `waiting_on_author`; GLI-5 queues the check on bot hooks for MRs in `waiting_on_author`; GLI-14 covers every order of comment, verdict note and approval hook. Session 4 T3 detects new commits by head `sha`. Assumption to verify in the smoke test: GitLab writes reviewer-authored system notes for approve and request changes.

## 2026-11-02 13:11 UTC — spec delta approved
sha256 e765594eeb05

## 2026-11-02 — Session 3 second delta re-review + spec delta: `reviewed` freshness
deep-reviewer second delta re-review: High A closed for every ordering (re-run of the repro); B, D, E closed; INV-1 holds; safety net admits no old approval while webhooks arrive. Open: [Medium] `reviewed` (comment-only) never had a fresh verdict time → silently waits; [Medium] Session 4 plan used list `updated_at` as entry time, which a later bot approval also moves; [Low] substring matching ("unapproved…" contains "approved…").
Spec delta (approved by the developer, "support both"): GLI-6 uses the verdict system note for `approved`/`requested_changes` and the bot's latest plain comment for `reviewed`, both read before the state. Session 4 T3 takes the push time from the newest MR diff version. Medium fixes need Gate A only (workflow §3).

## 2026-11-02 13:46 UTC — spec delta approved
sha256 287922b48d06

## 2026-11-02 — Session 3 (AI gate) Gate A
tests `scripts/test.sh -q` PASS (45 passed, 105 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 3` PASS (5/5: GLI-5, 6, 7, 12, 14). Manual scenario B re-run end to end against the mock: PASS.

## 2026-11-02 — Session 3 Gate B
Two Confirmed Highs, both closed after delta re-reviews by deep-reviewer (stale verdict after a push; fresh comment paired with stale state). Final round Mediums (`reviewed` freshness, Session 4 entry-time source) fixed by spec delta and Gate A; Low prefix matching fixed. No open Confirmed Critical/High.
Deferred: a check makes two paged note scans (one helper, kept simple); the row-lock test asserts the FOR UPDATE query rather than racing; GitLab system-note texts and reviewer-authored system notes are assumptions for the smoke test (Session 4 T6).
Session 3: done.

## 2026-11-02 — Session 4 Gate B round 1 + spec delta
reviewer (spec, style, query): COMMENT — GLI-9 wording vs the reconcile check; one versions call per listed MR; smoke checklist misses /versions, sha, last_commit, updated_at moves, sort/updated_after; check 1 would queue real writes.
deep-reviewer (logic, security): REQUEST_CHANGES. [High, confirmed] reconcile never queues a check for an MR in `waiting_on_author`, so a lost late bot approval is never recovered. [High, confirmed] any non-GitLab error in reconciliation crashes the worker on every start (crash loop stops all processing). [Medium, reproduced] offset paging with order_by=updated_at skips an MR updated mid-run and the cursor passes it. [Medium] a stale reconcile snapshot rolls `head_sha` back and reopens the gate. [Medium] push-detection branches untested; e2e subtest does not assert the middle state. [Low] no cursor lag; lock edge cases; reconcile blocks the loop; `reconcile` action accepted from webhooks.
Spec delta (approved by the developer): GLI-5 also queues a check for reconcile events on `waiting_on_author`; GLI-9 queues a check for a reconcile-created MR; Contracts list the reconcile payload, `head_sha`, `ReconcileCursor`, `/versions`, and `RMO_RECONCILE_LAG_SECONDS` (60).

## 2026-11-02 14:26 UTC — spec delta approved
sha256 5542ab55d1fc

## 2026-11-02 — Session 4 (reconciliation) Gate A
tests `scripts/test.sh -q` PASS (50 passed, 124 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace` PASS (14/14) · makemigrations --check clean · manual scenario B re-run against the mock PASS.

## 2026-11-02 — Session 4 Gate B
Round 1: two Confirmed Highs (no reconcile check for `waiting_on_author`; non-GitLab errors crash-looped the worker) + Mediums (paging skip — reproduced; stale snapshot; versions call per MR; tests; smoke checklist). Spec delta approved. Delta re-review: both Highs and all round-1 Mediums closed with evidence; INV-1, INV-2, INV-4 hold.
Delta round Medium (reproduced: >100 MRs in the lag window stalled every run) and two Lows (delayed push webhook moved head_sha back; version time of a newer push) fixed and verified by Gate A (Medium/Low: no re-review needed, workflow §3).
Deferred: real gateway paging through `/versions` has no direct unit test (covered by GLI-13 e2e); a permanent pile of >100 MRs with one exact `updated_at` still raises loudly; reconciliation blocks the worker loop while it runs (documented).
Session 4: done. Project: all sessions done. Remaining before production: the smoke test against the work GitLab (docs/testing.md §6).

## 2026-11-03 08:43 UTC — spec closed
Session archived: /home/tabdeal/side-projects/merge-orchestrator/.claude/craft/archive/session-20261103T084319+0000.json
