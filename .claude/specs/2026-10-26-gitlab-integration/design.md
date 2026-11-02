# DESIGN: GitLab integration

Spec: spec.md · Key: GLI

## Shape

- `integrations/gitlab.py` holds `GitLabRestGateway`, an httpx client that implements `GitLabGatewayInterface`. It also holds `GitLabError`. It has no domain imports. The interface gains one read method, `get_reviewer_state(project_id, iid, user_id) -> str | None`, and one list method, `list_open_merge_requests(project_id, updated_after, page) -> MergeRequestPage`, for the reconciler. Both fakes grow the same methods.
- The AI gate moves from labels to an outbox job. It follows the `resolve_areas` pattern: the handler reads GitLab with no transaction open, then applies the result under a row lock. The job lives in a new module, `reviews/ai_gate.py`, so that `lifecycle.py` stays about events.
- Reconciliation is `reviews/reconciler.py`, called from `run_worker` on a timer. It turns GitLab's MR list into inbox events (source `reconcile`), so the lifecycle stays the only writer of MR state (INV-4). It lives in `reviews`, not `integrations`, because it builds inbox payloads that the lifecycle understands.

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| The client is one `httpx.Client(base_url=GITLAB_URL + "/api/v4", headers={"PRIVATE-TOKEN": …}, timeout=RMO_GITLAB_TIMEOUT_SECONDS)` per gateway instance. The instance is cached per process by the existing `get_gitlab_gateway()` | `integrations.gitlab` | INV-3, GLI-4 | A new client per call (no pooling) |
| Errors: `response.is_success` false raises `GitLabError(f"{method} {path} -> {status}")`, built from the path only, never headers or the full URL. `httpx.TimeoutException` and `httpx.TransportError` are re-raised as `GitLabError` with the same message shape, from `None`, so the original exception (which can carry request data) is not chained into `last_error` | `integrations.gitlab` | INV-3, GLI-4 | Letting httpx exceptions reach `last_error` |
| Pagination: follow `X-Next-Page` until it is empty, with `per_page=100` | `integrations.gitlab` | GLI-1 | Assuming one page |
| `get_changes` reads `GET /projects/:id/merge_requests/:iid/diffs`. Files = `new_path`, plus `old_path` when `renamed_file`. Lines = count of lines starting with `+` or `-` in each `diff` (GitLab's `diff` field starts at the `@@` hunk and has no `---`/`+++` file headers, so none are skipped; changed in Session 2). `too_large = any(d.get("too_large") or d.get("collapsed"))`. `Changes` gains `too_large: bool = False` | `integrations.gitlab`, `integrations.adapters` | GLI-1, GLI-2 | MR `changes_count` (a file count, capped and stringly typed) |
| `set_reviewer`: `GET` the MR, read `reviewers[].id`, return when the id is already there, else `PUT` with `reviewer_ids` = current + new. A human editing reviewers between GET and PUT can lose that edit; accepted, because the outbox call is idempotent and the window is milliseconds | `integrations.gitlab` | GLI-3 | A blind `PUT reviewer_ids=[new]` (removes the AI Reviewer) |
| `get_reviewer_state` reads `GET /projects/:id/merge_requests/:iid/reviewers` and returns the `state` of the entry whose `user.id` matches, or `None` | `integrations.gitlab` | GLI-6 | Reading approvals only (cannot see "requested changes") |
| Ingress derives `mr_key` for a Note Hook too, when `object_attributes.noteable_type == "MergeRequest"`, from `project.id` and `merge_request.iid`. A Note Hook on an MR that lacks them gets 400 (RMO-20 extended to the fields ingress reads) | `reviews.views` | GLI-5 ordering per MR | Ignoring Note Hooks |
| Lifecycle: for a Merge Request Hook with any action other than `open`, or a Note Hook, on an MR in `awaiting_ai_review`, enqueue `check_ai_review`. A Merge Request Hook `update` with `object_attributes.oldrev` set, on an MR in `waiting_on_author`, moves it to `awaiting_ai_review` and enqueues `check_ai_review` (the bot may already have approved the new head) | `reviews.lifecycle` | GLI-5, GLI-7 | Keeping the label path beside it |
| `check_ai_review` handler: read the state outside a transaction; then in one `atomic`, lock the MR (`of=("self",)`) and apply only when it is still `awaiting_ai_review`. Pass → `awaiting_booking`, audit, enqueue `resolve_areas`. Fail → `waiting_on_author`, audit, notify the author | `reviews.ai_gate` | INV-1, GLI-6 | Reading GitLab inside the inbox transaction |
| Reconciler: one `pg_try_advisory_lock(RECONCILE_LOCK_KEY)` (session-level, released in `finally`) around the whole run; no transaction is open while it holds the lock across HTTP calls. Each worker keeps a monotonic timer and tries at most every `RMO_RECONCILE_INTERVAL_SECONDS` | `reviews.reconciler` | GLI-10, INV-1 | `pg_advisory_xact_lock` (would need a transaction across HTTP) |
| Per project, list `state=opened&order_by=updated_at&sort=asc&updated_after=<cursor>` page by page. For each page, in one `atomic`, store one inbox event per MR with `event_uuid = f"{project_id}:{iid}:{updated_at}"`, source `reconcile`, kind `Merge Request Hook`, `mr_key` as for webhooks, payload `{"project": {"id"}, "object_attributes": {"iid", "action": "reconcile", "author_id", "title"}}`, then set the cursor to the page's last `updated_at`. A GitLab error stops that project's run, logs, and leaves the committed cursor | `reviews.reconciler` | INV-2, GLI-8, GLI-11 | Writing MR rows directly; advancing the cursor once per run |
| `ReconcileCursor(project_id unique, updated_after nullable)`. Null means "read all open MRs" | `reviews.models` | GLI-8 | A settings value |
| Lifecycle treats action `reconcile` as `open` for an unknown MR (GLI-9), and as a non-open update for a known MR (so GLI-5 queues a check) | `reviews.lifecycle` | INV-4 | A separate reconcile code path |
| Unit tests of the gateway use `respx` (errors, timeouts, paging edge cases). Integration and end-to-end tests use `gitlab_mock`, a FastAPI service with GitLab REST v4 payload shapes from the docs, trimmed to the fields read | tests | — | A live GitLab; respx only (never exercises real HTTP or webhooks) |
| `gitlab_mock` is a separate top-level package, not a Django app: its own FastAPI app factory, in-memory state, uvicorn in a thread for tests, and a CLI for manual demos. It imports nothing from the orchestrator, so it cannot accidentally share state or settings | `gitlab_mock` | test isolation | A Django app inside the project; a Docker image (registry unavailable here) |
| End-to-end tests (`tests_e2e/`) run the orchestrator under `LiveServerTestCase`, so the mock's webhooks reach a real HTTP endpoint. The worker runs in-process (`run_worker --once`) | tests | GLI-12, GLI-13 | Calling views directly (skips the HTTP path that GLI-12 exists to prove) |

## Data and migrations

- `reviews.ReconcileCursor`: `project_id` (BigInteger, unique), `updated_after` (DateTime, null). One row per configured project. This is one new table in one `reviews` migration.
- No change to existing tables. `Changes` is a dataclass, not a model.

Rollback: set `RMO_GITLAB_GATEWAY` back to the fake and drop the cursor table. The label settings are gone, so a rollback to the label gate is a revert of Session 2.

## Standards review

| Standard | Verdict |
|---|---|
| django: Configuration and business constants | satisfied — `GITLAB_URL`/`GITLAB_TOKEN` read from the environment only in `settings.py`; timeout, interval, bot id, project ids in settings (S1-T1, S3-T1) |
| django: Transactions (no network inside atomic) | satisfied — gateway calls only in outbox handlers and the reconciler, outside `atomic` (S2-T3, S3-T2); reconciler holds a session-level lock, not a transaction |
| django: thin views | satisfied — ingress only gains `mr_key` derivation for Note Hooks (S2-T1) |
| django: service layer | satisfied — `ai_gate.py`, `reconciler.py` |
| python: Object-oriented design (`Protocol`, `Interface`) | satisfied — interface extended; real and fake implement it (S1-T2) |
| python: Logging | satisfied — reconciler logs project id and error; the token is never logged (S1-T3, S3-T2) |
| python: Modular design | satisfied — `integrations.gitlab` has no domain imports; reconciler lives with the lifecycle it feeds |
| TS-6 long-running job | satisfied — no manual reconnect; the advisory lock is released in `finally` and dies with the connection if the process dies |
| TS-3 retention | at risk — reconciliation adds inbox rows every interval for every updated MR → bounded by "updated since cursor"; retention stays out of scope, as before |
| implementation: Tests | satisfied — one test per GLI criterion, respx fixtures with literal payloads |

Completeness:
- Models and migrations: S3.
- Service logic: S1–S3.
- Endpoints: ingress changes in S2.
- Error handling: S1-T3, S3-T2.
- Concurrency: S3 lock.
- Validation: settings presence (S1-T3, S2-T3).
- Tests: one per criterion.
- Feature flag: `RMO_GITLAB_GATEWAY`.
- Logging: S1, S3.
- Metrics: N/A.
- Rollback: above.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Reviewer state names or the `/reviewers` shape differ on the real instance | Medium | High — the gate never passes | Unknown states leave the MR unchanged (no wrong booking); smoke test step in `docs/testing.md`; the state mapping is one dict |
| `updated_after` is inclusive, so the last MR of a page is re-listed next run | High | Low | Event UUID includes `updated_at`, so the replay is deduplicated |
| GET-then-PUT reviewer race with a human edit | Low | Low | Accepted; documented above |
| Changing to the bot-state gate breaks prototype tests that drive MRs through labels | Certain | Low | S2-T5 rewrites those tests to drive the gate through the fake's reviewer state |
| A first reconcile on a large project lists many open MRs | Low | Low | Paged, idempotent, and each page commits its own cursor |
