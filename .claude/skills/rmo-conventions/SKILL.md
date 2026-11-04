---
name: rmo-conventions
description: "How code and tests are written in merge-orchestrator: where logic lives, locks/transactions/outbox/audit patterns, GitLab adapter and fakes, settings, admin, panel, Django TestCase style with criterion IDs, concurrency tests, plus minimal-code, validation and test discipline. Load before writing or changing any Python, template, migration or test in this repository."
---

# rmo-conventions

## Write the least code that meets the spec

The ladder: stop at the first rung that works.
1. Reuse a function in `reviews/`, `audit/` or `integrations/`.
2. Use Django or the stdlib.
3. Write one line.
4. Write the minimum new function, in the module that owns the concept.

Rejected: a new helper module for one caller, a generic "manager" class, a
speculative parameter or setting, re-validating what a constraint already
guarantees, a wrapper around the ORM. Never trade correctness for brevity: the
locks, the audit and the outbox rows are never "extra".

## The write path

The reference shapes are `merge_confirmation.confirm_merge` and `merge_retry._route_one`.

```python
verdict = get_gitlab_gateway().read_x(...)          # 1. GitLab first, no transaction (TX-2)
with transaction.atomic():                          # 2. one atomic block (TX-1)
    mr = MergeRequest.objects.select_for_update().get(pk=mr_id)   # 3. MR lock first (LOCK-1)
    lock_booking()                                  #    only if booking, capacity or merges are touched (LOCK-3)
    mr.refresh...  / re-check eligibility here      # 4. read again under the lock (LOCK-2)
    before = snapshot(mr)                           #    audit "before" read under the lock
    ... change state ...
    audit.services.record(actor, "action.name", mr, before, after)   # DATA-1
    alerts.queue_notification(...) / outbox.enqueue(kind, payload)   # TX-3
```

- **Outbox handlers** are idempotent, raise on failure (the backoff retries
  them), are registered in `reviews/apps.py`, and are listed under Contracts.
  A busy session-level advisory lock raises a retryable error. Never wait for it.
- **Batches** (retry, reconcile) catch exceptions per item, log them, alert
  once, and go on (IO-2).
- **Time:** `timezone.now()` and `timezone.localdate()` only. Merge times are
  local `HH:MM` strings (DATA-2).
- **GitLab:** go through `integrations.adapters.get_gitlab_gateway()`. A new read
  is added to `GitLabGatewayInterface`, `GitLabRestGateway`, `FakeGitLabGateway`
  and `gitlab_mock/app.py` together, with a smoke-test item in `docs/testing.md` §6.
  Never add a merge method (GL-3).
- **Settings:** an `RMO_*` constant in `orchestrator/settings.py`, read from the
  environment there only, and failing at start-up when invalid.
- **Admin:** `ReadOnlyAdmin` for domain models. Actions use
  `@admin.action(description=..., permissions=["change"])`, call a service, and
  report with `message_user`.
- **Panel:** views subclass `PanelAccessMixin` and filter on `self.person`
  (AUTH-1). POSTs call a `reviews` service (AUTH-4). Templates extend
  `panel/base.html`. GitLab strings are never `|safe`.
- **Migrations:** `makemigrations` per app. Use `db_default` for a new non-null
  field. A data migration is `RunPython` with `migrations.RunPython.noop` as the
  reverse. Use a partial `UniqueConstraint` for "one open X" (LOCK-5).

## Validation

Add a check only when it is **REQUIRED** (security, data integrity, an explicit
contract) or **USEFUL** (it turns a confusing failure into a clear one). Put it
in the layer that owns the invariant: a constraint or model for data, a service
for rules, a view or form for input shape. Do not re-check what the layer below
already guarantees. Write the meaningful decisions under "Validation decisions" in the spec.

## Tests

- **One test per criterion.** The ID goes first in the docstring:
  `"""MRDR-3: Given ..., when ..., then ...."""`. Use `# given / # when / # then`
  in the body, and `subTest` for tables. Add no test without a criterion, except
  the LOCK-6 concurrency tests the spec names as invariants.
- `django.test.TestCase` by default. Use `TransactionTestCase` with two threads
  for anything that relies on a lock, `skip_locked` or a constraint race. Then
  remove the lock locally and confirm the test fails (LOCK-6). Say in your report
  that you did.
- Use the module-level builders in the test file (`create_merge_request`,
  `merge_owner`, `hook_event`, ...) and `panel/tests/helpers.panel_login`.
  Builders take their values as arguments. Keep tests DAMP: no loops,
  conditionals or computed expectations.
- For GitLab, use `mock.patch.object(<module>, "get_gitlab_gateway",
  return_value=FakeGitLabGateway())` and `override_settings(RMO_...=...)`. The
  e2e (`tests_e2e/`) uses `gitlab_mock.testing.running_mock` and clears the
  gateway cache.
- Scoping tests always have a decoy row that belongs to someone else. A panel
  POST has anonymous and unlinked-user cases (AUTH-5). Time logic has a midnight
  Asia/Tehran case.
- Do not test Django itself, admin registration, or a field's existence.
- Tests need Postgres: run them through `rmo check tests -- <file>`, never bare pytest.

## Comments and docstrings

Write short, plain sentences: one idea each, active voice, present tense. A
service docstring says what the function guarantees and names the criterion or
INV it enforces. A comment explains *why*, never *what*.
