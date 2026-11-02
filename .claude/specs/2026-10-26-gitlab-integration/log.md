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
