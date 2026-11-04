---
name: rmo-debug
description: "Evidence-first debugging for merge-orchestrator: a failing or flaky test, an MR stuck in a state, a quarantined inbox event, a FAILED outbox job, a missing chat or alert, a reconcile or webhook problem, a deadlock. Use it when the user runs /rmo-debug or reports a bug or a failure here."
argument-hint: <symptom>
---

# rmo-debug

Do not guess and patch. Reproduce, locate, explain, then fix with a regression test.

## 1. Reproduce

- **A test:** `.claude/rmo/bin/rmo check tests -- <file>::<Class>::<test>`. The full log is
  in `.claude/rmo/state/logs/`. A flaky failure in a `TransactionTestCase` suggests
  a race or a lock-order issue (LOCK-1). Run it 5–10 times before you conclude anything.
- **A runtime symptom:** use the compose stack and the mock (README, "Run it with
  Docker Compose"), and drive it with `scripts/gitlab-mock` (open, approve, merge).
  `docs/testing.md` §4 has a scenario for each feature. Start from the closest one.

## 2. Look where this system keeps its evidence

| Symptom | Look at |
|---|---|
| MR does not move | the `InboxEvent` rows for its `mr_key`: an earlier event with `failed_at` set blocks every later one (quarantine). Also `processed_at`, and the claim grace |
| Job did not happen | the outbox rows by kind: `attempts`, `last_error`, lease expiry, `FAILED`. Is the handler registered in `reviews/apps.py`? |
| Wrong state or decision | the `AuditEvent` rows for the MR (`before`/`after`). They are the ground truth of who changed what, and when |
| Approval ignored or wrongly counted | freshness: `coverage.is_fresh` against `ai_gate_entered_at` and `head_sha_at` (GL-2) |
| Merge not recorded, or the wrong merger | the `confirm_merge` job, `merge_user`, then `merged_by` (GL-1) |
| No merge owner | `merge_router` eligibility: author or reviewer excluded, leave, working days, daily limit, horizon. Then the daily retry state (`last_retry_on`) |
| Deadlock, lock wait or lease expiry | lock order (LOCK-1), a GitLab call inside a transaction (TX-2), a blocking advisory lock (LOCK-4) |
| Wrong day or window | a naive datetime or a UTC date where `timezone.localdate()` belongs (DATA-2) |
| Panel shows too much or too little | the queryset scoping on `self.person` and the role (AUTH-1, AUTH-2) |

Use `docker compose exec -T web python manage.py shell` (or `.venv/bin/python
manage.py shell` with `DEBUG=1 DB_HOST=$PWD/.pgsock`) for read-only queries.
Never "fix" data by hand without the developer's consent.

## 3. Explain before you fix

State the root cause in one or two sentences, with `file:line` and the input or
interleaving that triggers it. When the root cause is not proven, say what
evidence is missing and get it.

## 4. Fix through the workflow

- A one-line fix with a proven cause, outside the "quick may never touch" list,
  goes on the quick route.
- Everything else goes on task. The regression test is a criterion of class
  REGRESSION that fails before the fix. When an approved spec covers the
  behaviour, the fix may need a spec delta instead.
