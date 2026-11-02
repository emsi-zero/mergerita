# TASKS: GitLab integration

Spec: spec.md · Key: GLI · Route: project · State: done
Checkpoint: `scripts/test.sh -q`

## Session 1 — Mock GitLab service

State: done · Covers: none (test scaffolding for GLI-12, GLI-13)

- [x] T1 — Add `requirements-dev.txt` (`-r requirements.txt`, fastapi, uvicorn, httpx, respx) and install it into `.venv`; `scripts/test.sh` keeps working
- [x] T2 — `gitlab_mock/app.py`: FastAPI app factory `create_app(token, webhook_url, webhook_secret)` with in-memory state (projects, users, MRs with diffs, reviewers and their states, head sha, `updated_at`). GitLab REST v4 routes under `/api/v4`: MR diffs (paged, `X-Next-Page`), MR reviewers, MR get, MR update (`reviewer_ids`), project MR list (`state`, `updated_after`, `order_by`, `sort`, paged). `PRIVATE-TOKEN` checked; 401 otherwise
- [x] T3 — Control API under `/_mock/`: create MR (sends Merge Request Hook `open`), set a reviewer's state (`approved` sends Merge Request Hook `approval`; `requested_changes` or `reviewed` sends a Note Hook from that user), push (new sha, sends Merge Request Hook `update` with `oldrev`), add note (Note Hook), `webhooks on|off`, fault injection (`next N calls → status S`, or `timeout`), and state dump. Webhooks are sent with `X-Gitlab-Token`, `X-Gitlab-Event`, and a fresh `X-Gitlab-Event-UUID`
- [x] T4 — `gitlab_mock/testing.py`: a helper that starts the app with uvicorn in a thread on a free port and stops it, for tests
- [x] T5 — `scripts/gitlab-mock`: CLI with `serve` (host, port, token, webhook url and secret) and the control commands `open`, `approve`, `request-changes`, `push`, `note`, `state`, `webhooks on|off`, `fail`
- [x] T6 — `gitlab_mock/tests/test_smoke.py`: one smoke test per route group (diffs paging, reviewer PUT, list filter, a webhook delivered to a local receiver) — scaffolding check, no criterion ID

Gate A: checkpoint green through `bin/craft-check` · pylint (add `gitlab_mock`), isort, black green
Gate B: `reviewer` (lenses: spec, style) — scaffolding, no deep review

Resume: `gitlab_mock` runs standalone and in tests (`running_mock()` in `gitlab_mock/testing.py` yields the base URL; control API under `/_mock/`; CLI `scripts/gitlab-mock`; run tests with `DEBUG=1`). Next: Session 2, the real gateway, tested against the mock and respx.

## Session 2 — Real GitLab REST gateway

State: done · Covers: GLI-1, GLI-2, GLI-3, GLI-4

- [x] T1 — Add `httpx` to `requirements.txt` (respx is already in `requirements-dev.txt`). Add `GITLAB_URL`, `GITLAB_TOKEN` (environment) and `RMO_GITLAB_TIMEOUT_SECONDS = 10` to `orchestrator/settings.py` — contracts
- [x] T2 — In `integrations/adapters.py`, add `too_large: bool = False` to `Changes`, and add `get_reviewer_state` and `list_open_merge_requests` (with a small `MergeRequestPage` / `MergeRequestSummary` dataclass) to `GitLabGatewayInterface`. Implement both in `integrations/fakes.py` (seedable dicts) — GLI-1, GLI-6, GLI-8
- [x] T3 — Add `integrations/gitlab.py`: `GitLabError`, and `GitLabRestGateway` with `get_changes` (paged diffs, renames, line count, `too_large`), `set_reviewer` (GET then PUT, keep existing, no-op if present), `get_reviewer_state`, `list_open_merge_requests`, and `post_note` raising `NotImplementedError`. The constructor checks `GITLAB_URL`/`GITLAB_TOKEN`. Errors are built from method, path and status only — GLI-1, GLI-3, GLI-4, INV-3
- [x] T4 — In `reviews/area_resolver.py`, treat `changes.too_large` as over the size limit — GLI-2
- [x] T5 — Tests in `integrations/tests/test_gitlab.py` (respx) for GLI-1, GLI-3, GLI-4, and a GLI-2 test in `reviews/tests/test_area_resolver.py`

Gate A: `bin/craft-spec trace --session 2` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style) · `deep-reviewer` (logic, security on INV-3 and the reviewer merge)

Resume: `GitLabRestGateway` exists and is selectable by `RMO_GITLAB_GATEWAY`; the fake stays the default. Next: Session 3, the AI gate. Gateway tests need no DB (SimpleTestCase) and use `running_mock` plus respx.

## Session 3 — AI gate on the bot's review state

State: done · Covers: GLI-5, GLI-6, GLI-7, GLI-12, GLI-14

- [x] T1 — In `reviews/views.py`, derive `mr_key` for a Note Hook on an MR (`noteable_type == "MergeRequest"`) from `project.id` and `merge_request.iid`; missing fields give 400 — GLI-5
- [x] T2 — In `reviews/lifecycle.py`, remove `_apply_labels`. A Merge Request Hook (any action except `open`) or a Note Hook on an MR in `awaiting_ai_review` enqueues `check_ai_review`. A Merge Request Hook `update` with `oldrev` on an MR in `waiting_on_author` moves it to `awaiting_ai_review`, records audit, and enqueues `check_ai_review` — GLI-5, GLI-7
- [x] T3 — Add `reviews/ai_gate.py` with the `check_ai_review` handler: read `get_reviewer_state(…, settings.RMO_AI_REVIEWER_GITLAB_USER_ID)` with no transaction open, then in one `atomic` lock the MR and apply pass or fail only when it is still `awaiting_ai_review`. Register it in `reviews/apps.py` — GLI-6
- [x] T4 — In `orchestrator/settings.py`, remove `RMO_AI_PASS_LABEL`/`RMO_AI_FAIL_LABEL` and add `RMO_AI_REVIEWER_GITLAB_USER_ID` (from the environment, with no default) — contracts
- [x] T5 — Rewrite the prototype tests that drive the gate through labels (`reviews/tests/test_lifecycle.py` RMO-7, `reviews/tests/test_audit_trail.py`) to use the fake's reviewer state. Remove RMO-7's label test, which GLI-6 replaces. Add tests for GLI-5, GLI-6 and GLI-7 in `reviews/tests/test_ai_gate.py`
- [x] T6 — End-to-end test GLI-12 in `tests_e2e/test_gitlab_flow.py`: a `LiveServerTestCase` with `RMO_GITLAB_GATEWAY` set to the real gateway pointed at `gitlab_mock`. The mock opens an MR and the bot approves it; the test runs the worker and asserts the reviewers in the mock's state — GLI-12
- [x] T7 — Update `docs/testing.md` and `README.md`: the manual demo now uses `scripts/gitlab-mock` (serve, open, approve, …) in place of `label_mr` and shell seeding

Gate A: `bin/craft-spec trace --session 3` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1 and the state mapping)

Resume: The AI gate acts only on a fresh verdict: `ai_gate` reads the bot's latest verdict system note time (`get_latest_verdict_time`), then its state, and acts when the note is newer than `MergeRequest.ai_gate_entered_at` (GitLab's clock, set on open and on pushes by `lifecycle._reopen_ai_gate`, which locks the row and never moves the time back). A bot hook on a `waiting_on_author` MR queues a check, and a fresh `approved` books it. Mock `approve`/`request-changes` write the system notes. Assumption to verify in the smoke test (Session 4 T6): the note texts in `integrations/gitlab.py`. Next: Session 4, reconciliation.

## Session 4 — Reconciliation

State: done · Covers: GLI-8, GLI-9, GLI-10, GLI-11, GLI-13

- [x] T1 — Add the `ReconcileCursor` model and its `reviews` migration. Add `RMO_GITLAB_PROJECT_IDS` (environment, comma-separated ints, default empty) and `RMO_RECONCILE_INTERVAL_SECONDS = 300` to settings — GLI-8
- [x] T2 — Add `reviews/reconciler.py` `reconcile_all()`:
  - Wrap the whole run in a session `pg_try_advisory_lock`; return when another worker holds it.
  - Per project, fetch pages with no transaction open.
  - Per page, in one `atomic`, call `store_event` for each MR (source `reconcile`, UUID `project:iid:updated_at`), then advance the cursor.
  - On `GitLabError`, log and move on to the next project.

  Covers GLI-8, GLI-10, GLI-11, INV-2, INV-4.
- [x] T3 — In `reviews/lifecycle.py`, action `reconcile` creates an unknown MR (the same as `open`) and counts as an update for a known MR — GLI-9. Store the MR head `sha` on `MergeRequest` (from webhooks and the MR list); a reconciled MR whose `sha` changed counts as new commits and goes through `_reopen_ai_gate`. The entry time is the push itself — the newest MR diff version's `created_at` (`GET …/merge_requests/:iid/versions`) — not the list's `updated_at`, which a later bot approval also moves. Write `sha` from the open hook, the push hook (`last_commit.id`) and the list; a null stored `sha` (MRs from before this session) is recorded without counting as a push. GLI-13 gets a subTest: webhooks off, push, bot approves, reconcile → `awaiting_booking` (Session 3 deep review, issue C, twice) — GLI-7, GLI-13
- [x] T4 — In `reviews/management/commands/run_worker.py`, call `reconcile_all()` when the interval has passed (monotonic timer), and also once in `--once` mode
- [x] T5 — Tests in `reviews/tests/test_reconciler.py` for GLI-8, GLI-9, GLI-10 (`TransactionTestCase`, lock held by a second connection), GLI-11
- [x] T5b — End-to-end test GLI-13 in `tests_e2e/test_gitlab_flow.py`: mock webhooks off, open and approve an MR, run reconciliation and the worker, and assert the same booked state as GLI-12 — GLI-13
- [x] T6 — Add a "smoke test against the work GitLab" section to `docs/testing.md`: the settings to set, and the checks to make for the reviewer-state names and for a reviewer merge that keeps the AI bot

Gate A: `bin/craft-spec trace --session 4` green · `bin/craft-spec trace` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-2, INV-4 and the lock)

Resume: Code and tests done; Gate A recorded; Gate B (reviewer, deep-reviewer) not run. Reconciliation: `reviews/reconciler.py` (`reconcile_all`, session advisory lock 7302, page + cursor in one atomic), called by `run_worker` on a monotonic timer and once in `--once`. The reconciler reads the newest diff version time (`get_latest_version_time`) outside the transaction and puts it in the payload as `version_created_at`; lifecycle uses it as the gate entry time and compares `sha` with `MergeRequest.head_sha`. A reconcile-created MR also queues a check (the bot may have approved already). Smoke checklist: `docs/testing.md` section 6. Gate B round 1 fixes applied (keyset paging with `RMO_RECONCILE_LAG_SECONDS`, any-exception guard per project, `head_sha_at` stale-snapshot rule, versions call only for unknown or changed sha, reconcile checks `waiting_on_author`, source check); deep-reviewer re-reviews the Highs. Next: re-review, then `/finish`.
