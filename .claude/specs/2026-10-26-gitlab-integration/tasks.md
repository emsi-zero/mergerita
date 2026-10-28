# TASKS: GitLab integration

Spec: spec.md · Key: GLI · Route: project · State: pending
Checkpoint: `scripts/test.sh -q`

## Session 1 — Real GitLab REST gateway

State: pending · Covers: GLI-1, GLI-2, GLI-3, GLI-4

- [ ] T1 — Add `httpx` and `respx` to `requirements.txt` and install them into `.venv`. Add `GITLAB_URL`, `GITLAB_TOKEN` (environment) and `RMO_GITLAB_TIMEOUT_SECONDS = 10` to `orchestrator/settings.py` — contracts
- [ ] T2 — In `integrations/adapters.py`, add `too_large: bool = False` to `Changes`, and add `get_reviewer_state` and `list_open_merge_requests` (with a small `MergeRequestPage` / `MergeRequestSummary` dataclass) to `GitLabGatewayInterface`. Implement both in `integrations/fakes.py` (seedable dicts) — GLI-1, GLI-6, GLI-8
- [ ] T3 — Add `integrations/gitlab.py`: `GitLabError`, and `GitLabRestGateway` with `get_changes` (paged diffs, renames, line count, `too_large`), `set_reviewer` (GET then PUT, keep existing, no-op if present), `get_reviewer_state`, `list_open_merge_requests`, and `post_note` raising `NotImplementedError`. The constructor checks `GITLAB_URL`/`GITLAB_TOKEN`. Errors are built from method, path and status only — GLI-1, GLI-3, GLI-4, INV-3
- [ ] T4 — In `reviews/area_resolver.py`, treat `changes.too_large` as over the size limit — GLI-2
- [ ] T5 — Tests in `integrations/tests/test_gitlab.py` (respx) for GLI-1, GLI-3, GLI-4, and a GLI-2 test in `reviews/tests/test_area_resolver.py`

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style) · `deep-reviewer` (logic, security on INV-3 and the reviewer merge)

Resume: `GitLabRestGateway` exists and is selectable by `RMO_GITLAB_GATEWAY`; the fake stays the default. Next: Session 2, the AI gate.

## Session 2 — AI gate on the bot's review state

State: pending · Covers: GLI-5, GLI-6, GLI-7

- [ ] T1 — In `reviews/views.py`, derive `mr_key` for a Note Hook on an MR (`noteable_type == "MergeRequest"`) from `project.id` and `merge_request.iid`; missing fields give 400 — GLI-5
- [ ] T2 — In `reviews/lifecycle.py`, remove `_apply_labels`. A Merge Request Hook (any action except `open`) or a Note Hook on an MR in `awaiting_ai_review` enqueues `check_ai_review`. A Merge Request Hook `update` with `oldrev` on an MR in `waiting_on_author` moves it to `awaiting_ai_review`, records audit, and enqueues `check_ai_review` — GLI-5, GLI-7
- [ ] T3 — Add `reviews/ai_gate.py` with the `check_ai_review` handler: read `get_reviewer_state(…, settings.RMO_AI_REVIEWER_GITLAB_USER_ID)` with no transaction open, then in one `atomic` lock the MR and apply pass or fail only when it is still `awaiting_ai_review`. Register it in `reviews/apps.py` — GLI-6
- [ ] T4 — In `orchestrator/settings.py`, remove `RMO_AI_PASS_LABEL`/`RMO_AI_FAIL_LABEL` and add `RMO_AI_REVIEWER_GITLAB_USER_ID` (from the environment, with no default) — contracts
- [ ] T5 — Rewrite the prototype tests that drive the gate through labels (`reviews/tests/test_lifecycle.py` RMO-7, `reviews/tests/test_audit_trail.py`) to use the fake's reviewer state. Remove RMO-7's label test, which GLI-6 replaces. Add tests for GLI-5, GLI-6 and GLI-7 in `reviews/tests/test_ai_gate.py`
- [ ] T6 — Update `docs/testing.md` and `README.md`: replace `label_mr` with a Note Hook helper, and show how to seed the bot's state in the fake

Gate A: `bin/craft-spec trace --session 2` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1 and the state mapping)

Resume: The AI gate reads the bot's review state. Next: Session 3, reconciliation.

## Session 3 — Reconciliation

State: pending · Covers: GLI-8, GLI-9, GLI-10, GLI-11

- [ ] T1 — Add the `ReconcileCursor` model and its `reviews` migration. Add `RMO_GITLAB_PROJECT_IDS` (environment, comma-separated ints, default empty) and `RMO_RECONCILE_INTERVAL_SECONDS = 300` to settings — GLI-8
- [ ] T2 — Add `reviews/reconciler.py` `reconcile_all()`:
  - Wrap the whole run in a session `pg_try_advisory_lock`; return when another worker holds it.
  - Per project, fetch pages with no transaction open.
  - Per page, in one `atomic`, call `store_event` for each MR (source `reconcile`, UUID `project:iid:updated_at`), then advance the cursor.
  - On `GitLabError`, log and move on to the next project.

  Covers GLI-8, GLI-10, GLI-11, INV-2, INV-4.
- [ ] T3 — In `reviews/lifecycle.py`, action `reconcile` creates an unknown MR (the same as `open`) and counts as an update for a known MR — GLI-9
- [ ] T4 — In `reviews/management/commands/run_worker.py`, call `reconcile_all()` when the interval has passed (monotonic timer), and also once in `--once` mode
- [ ] T5 — Tests in `reviews/tests/test_reconciler.py` for GLI-8, GLI-9, GLI-10 (`TransactionTestCase`, lock held by a second connection), GLI-11
- [ ] T6 — Add a "smoke test against the work GitLab" section to `docs/testing.md`: the settings to set, and the checks to make for the reviewer-state names and for a reviewer merge that keeps the AI bot

Gate A: `bin/craft-spec trace --session 3` green · `bin/craft-spec trace` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-2, INV-4 and the lock)

Resume: Webhooks and reconciliation both feed the inbox. The real gateway is ready for a smoke test at work. Next: `/finish`.
