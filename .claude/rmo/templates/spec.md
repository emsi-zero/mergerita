# SPEC: {title}

Key: {key} · Route: {route} · Risk: low
Created: {date}

<!-- WHAT and WHY only. The approval is bound to this file's SHA-256: any edit
     closes the gate until the developer approves the delta. No state, no
     progress, no test names here. Delete every comment before approval:
     `rmo approve` refuses while one remains. -->

## Problem

<!-- 2-4 sentences: who is affected (author, reviewer, merge owner, admin),
     what goes wrong or is missing, how we know. -->

## Non-goals

<!-- Real temptations only. Typical here: the orchestrator does not merge;
     no new GitLab write; no panel write path for MR/booking data; no SSO. -->
- 

## Acceptance criteria

<!-- One observable behaviour per row, EARS form. Exactly one test per row,
     ID first in its docstring: """{key}-1: Given ..., when ..., then ...""".
     Observable = an MR state, a stored row, an audit event, an outbox job
     (notify, confirm_merge, ...), an HTTP status, a panel page.
     Classes: CRITICAL BEHAVIOR, BUSINESS RULE, SECURITY BOUNDARY,
     IMPORTANT FAILURE MODE, CONCURRENCY, REGRESSION, INTEGRATION CONTRACT. -->

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| {key}-1 | WHEN <trigger>, THE <component> SHALL <observable response>. | BUSINESS RULE | <the realistic bug this catches> |

Not tested, and why:
- 

## Invariants

<!-- Name the catalogue IDs from .claude/rmo/INVARIANTS.md this change must
     keep (e.g. LOCK-1, TX-2, GL-1), then add only what is new, with owner and
     mechanism: MR row lock, lock_booking(), partial unique constraint, one
     atomic block. -->
- Holds: <catalogue IDs>
- INV-1 — <invariant> — owned by <module> — enforced by <mechanism>

## Contracts

<!-- Every externally visible change: migrations (app + number), new audit
     actions, outbox job kinds and payloads, chat and alert texts, GitLab
     fields read, admin actions, panel URLs, settings and env vars, webhook
     responses. Past reviews flagged each of these when it was missing here. -->
None.

## Validation decisions

- <check> — REQUIRED | USEFUL — enforced in <layer>

## Docs

<!-- docs/testing.md scenario letter (the next free letter is in
     INVARIANTS.md, "Docs"), README section, smoke-test items for any new
     GitLab field. Write "None." when nothing a person runs changes. -->
- 

## Checkpoint

`.claude/rmo/bin/rmo gate-a` (tests `scripts/test.sh -q`, pylint, isort, black, makemigrations --check, trace)

## Open questions

| Question | Options | Decision |
|---|---|---|
