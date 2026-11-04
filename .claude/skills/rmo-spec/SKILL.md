---
name: rmo-spec
description: "Write, present, approve, or change (spec delta) the spec for a merge-orchestrator change: problem, non-goals, EARS acceptance criteria with IDs, invariants citing .claude/rmo/INVARIANTS.md, contracts, docs, risk tier. Use it on the task and project routes, when the user runs /rmo-spec, or when code shows an approved spec is wrong."
argument-hint: <what the change is>
---

# rmo-spec

`rmo` = `.claude/rmo/bin/rmo`. A finished example in this repository's style is
`.claude/specs/2026-11-04-merge-routing-daily-retry/spec.md`.

## 1. Create

`rmo new --route task|project --title "<title>" --key <KEY>`. Choose a short,
unused key (3–4 letters). The existing keys are RMO, GLI, DVP, MRV, PAU, RVO,
MRG, MRDR and PNS. The gate is now shut for every path outside `.claude/`.

## 2. Learn just enough

Read the service modules the change touches, their callers, and their tests
(`reviews/tests/test_<module>.py`). Read `.claude/rmo/INVARIANTS.md` and pick
the families that apply. Stop reading when every section can be filled.

## 3. Write spec.md

- **Criteria** — EARS, one observable behaviour per row: an MR state, a row, an
  audit event, an outbox job, an HTTP status, a panel page. Two to seven rows on
  task. A longer list means a project, or criteria that test implementation.
- **Always give a criterion to** any transition, race, scoping rule, idempotent
  re-run, or poison input the change introduces. Past reviews found these
  missing most often:
  - freshness of GitLab verdicts (GL-2),
  - a second concurrent caller (LOCK-*),
  - a malformed or unexpected input that must not stick an MR (IO-2),
  - anonymous, unlinked and someone else's object for every panel POST (AUTH-5).
- **Invariants** — "Holds:" lists the catalogue IDs, then add only the new rows.
- **Contracts** — list every migration, audit action, outbox kind, alert text,
  GitLab field, admin action, URL and setting. Omissions here caused
  documentation-only spec deltas twice.
- **Docs** — the scenario letter (the next free one is in INVARIANTS.md "Docs").
- **Risk** — apply WORKFLOW.md §2. Run `rmo risk` when code paths are known, and
  give the reason for the tier in one line under the header.
- Delete every template comment and placeholder. `rmo approve` refuses while any remain.

## 4. Present and stop

Show the criteria table, "Not tested, and why", the non-goals, the risk tier
with its reason, and the open questions with your proposed decisions. Give the
path for the rest. Ask for **approve**, **cut <IDs>**, or **add/change** (in any
form, English or Persian). A row the developer adds is required: mark it
`[developer]` and keep it. Write no code while you wait.

## 5. Approve

Only after the developer says approve: `rmo approve`. It records the SHA-256
and logs it. On project, go on to `rmo-plan`. On task, fill `tasks.md`.

## Spec delta

When code or a review shows the spec is wrong or incomplete:
1. Stop building the affected behaviour.
2. Show the delta in three lines or fewer: rows added, changed or removed, and why.
   Add the decisions you need, with options.
3. After the developer approves: edit `spec.md`, run `rmo log "spec delta" "<what and why>"`,
   then `rmo approve --note "delta: <summary>"`.

Removing a criterion needs the developer's explicit words.
