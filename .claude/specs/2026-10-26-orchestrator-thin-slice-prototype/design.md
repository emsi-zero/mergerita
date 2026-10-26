# DESIGN: Orchestrator thin-slice prototype

Spec: spec.md · Key: RMO

## Shape

One Django project (`orchestrator/`) with three apps, and one-way dependencies:
`reviews → integrations` and `reviews → audit`.

- `integrations` holds infrastructure with no domain knowledge: the `InboxEvent` and `OutboxCall` models, the adapter interfaces (`Protocol`), the fakes, and the outbox dispatcher. The dispatcher runs registered handlers.
- `audit` holds the append-only `AuditEvent` and one function, `audit.services.record()`.
- `reviews` holds the domain and its entry points: models, `lifecycle`, `area_resolver` and `booking` services, the webhook ingress view, the `run_worker` command, and the read API.

The ingress view lives in `reviews`, not `integrations`. It only stores an inbox row, but the worker that reads that row must dispatch into the domain. Putting both entry points in `reviews` keeps `integrations` free of domain imports.

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| Worker claims the oldest pending event whose id is the minimum pending id for its `mr_key`, with `FOR UPDATE SKIP LOCKED`, and handles and marks it in one `atomic` block | `reviews.management.commands.run_worker` → `reviews.lifecycle` | INV-1, RMO-5, RMO-6 | Plain `SKIP LOCKED` on the oldest event: a second worker could take MR X's event 2 while event 1 is in flight |
| Area resolution runs as an internal outbox job (`resolve_areas`), not inline in event handling. The handler calls `get_changes()` outside any transaction, then resolves and books in one `atomic` block, guarded by "MR has no area reviews yet" under a row lock on the MR | `reviews.area_resolver`, dispatched by `integrations.outbox` | No network call inside `atomic`; INV-4 | Calling the GitLab gateway inside the inbox transaction |
| Outbox dispatch: claim due rows in a short transaction (status `in_flight`, lease until now + 60 s), call the adapter with no transaction open, then record the result in a second short transaction. An expired lease makes the row claimable again | `integrations.outbox` | INV-5, RMO-16 | Holding `FOR UPDATE` across the adapter call |
| Retry backoff: `next_attempt_at = now + base × 2^attempts`, with base from settings `RMO_OUTBOX_BACKOFF_SECONDS`; `failed` after `RMO_OUTBOX_MAX_ATTEMPTS` | `integrations.outbox` | RMO-16 | Fixed interval |
| Adapters are `Protocol`s (`GitLabGatewayInterface`, `NotifierInterface`) chosen by dotted path in settings. Prototype values point at fakes | `integrations.adapters`, `integrations.fakes` | Domain never imports a vendor client | Hard-wiring the fakes |
| Booking stores reviewer, booked day, slot minutes and reason on `AreaReview`. Capacity is a `CapacityDay` row per person per date (`budget_minutes`, `booked_minutes`), created on demand and locked with `select_for_update` before it is incremented | `reviews.booking` | INV-2, INV-3 | A separate `Booking` table (no second consumer in the thin slice) |
| Candidates are ranked by a sort key tuple `(tier, -free_minutes, open_bookings, person_id)` on the earliest feasible day. The reason string is built from the same values | `reviews.booking` | RMO-13, RMO-14 | Weighted score: harder to explain |
| `AuditEvent.save()` refuses when `pk` is set; `delete()` and the queryset's `update()`/`delete()` raise | `audit.models` | INV-6 | A DB trigger (left as a risk; not needed for the prototype) |
| Foreign keys use `on_delete=DO_NOTHING, db_constraint=False`, following django_standards. Exception: the auto-created `AreaReview.areas` through table keeps real FK constraints (Django cannot set `db_constraint=False` there); accepted for the prototype | all models | — | `CASCADE` (forbidden by standard); an explicit through model (more code, no second consumer) |
| Read API: a hand-written serializer (plain function to dict) and a DRF `RetrieveAPIView` with `SessionAuthentication` + `IsAdminUser`, `select_related`/`prefetch_related` for area reviews, areas, reviewer | `reviews.views`, `reviews.serializers` | RMO-19, TS-5 | DRF `ModelSerializer` |
| Tests: Django `TestCase`/`TransactionTestCase`/`APITestCase` run by `pytest-django`; tooling `black`, `isort` (black profile), `pylint` + `pylint-django` | repo root | Gate A | — |

## Data and migrations

New database, so no downtime concerns (TS-1 not applicable). One initial migration per app.

- `integrations.InboxEvent`: `source`, `event_uuid`, `event_kind`, `mr_key` (nullable, `"<project_id>:<iid>"`), `payload` (JSON), `received_at`, `processed_at` (nullable). Unique on (`source`, `event_uuid`). Index on (`mr_key`, `id`) with condition `processed_at IS NULL`.
- `integrations.OutboxCall`: `kind`, `payload` (JSON), `status` (`pending`/`in_flight`/`done`/`failed`), `attempts`, `next_attempt_at`, `lease_until`, `last_error`, `created_at`. Index on (`status`, `next_attempt_at`).
- `audit.AuditEvent`: `actor`, `action`, `subject_type`, `subject_id`, `before` (JSON), `after` (JSON), `created_at`.
- `reviews.Person`: `gitlab_user_id` (unique), `name`, `working_days` (JSON list of weekday ints, default Sat–Wed), `daily_budget_minutes` (nullable, falls back to setting), `is_admin`.
- `reviews.Leave`: `person`, `start_date`, `end_date`.
- `reviews.CodeArea`: `name`, `path_patterns` (JSON list of globs), `size_limit_lines`.
- `reviews.Ownership`: `person`, `area`, `role` (`primary`/`backup`). Unique on (`person`, `area`).
- `reviews.MergeRequest`: `project_id`, `iid` (unique together), `author` (Person), `title`, `state` (TextChoices: `awaiting_ai_review`, `awaiting_booking`, `in_review`, `waiting_on_author`, `needs_triage`), `opened_at`, `changed_lines`.
- `reviews.AreaReview`: `merge_request`, `areas` (M2M CodeArea), `state` (`unbooked`, `booked`), `reviewer` (nullable), `booked_day` (nullable), `slot_minutes`, `reason`.
- `reviews.CapacityDay`: `person`, `day`, `budget_minutes`, `booked_minutes`. Unique on (`person`, `day`).

Volume: tens of rows a day. No retention plan is needed for the prototype (TS-3 noted as a risk for the inbox and audit tables).

Rollback: drop the database. Nothing else depends on it.

## Standards review

| Standard | Verdict |
|---|---|
| django: Configuration and business constants | satisfied — every rule value in `orchestrator/settings.py`, environment read only there (S1-T2) |
| django: No fat views | satisfied — the ingress view checks the token, stores, and returns; the API view only fetches and serializes (S1-T5, S4-T6) |
| django: Service layer | satisfied — logic in `lifecycle.py`, `area_resolver.py`, `booking.py`; models stay thin |
| django: Transactions (small, no nesting, no decorator, no network inside) | at risk — the worker's per-event `atomic` calls lifecycle, which must not open its own `atomic` → S2-T3 states that services take no transaction of their own, and the caller owns it; network calls are only in the outbox dispatcher, outside `atomic` (S3-T3) |
| django: Choice fields and state lists as `TextChoices` | satisfied (S2-T1, S3-T1) |
| django: FK `DO_NOTHING` + `db_constraint=False` | satisfied (all model tasks) |
| django: Custom serializers | satisfied (S4-T6) |
| django: i18n | not applicable — no user-facing text; API errors are DRF defaults |
| python: Modular design | satisfied — three apps, each owns its data and invariant (inbox/outbox delivery; audit append-only; review domain), one-way dependencies |
| python: Object-oriented design (`Protocol`, `Interface` suffix) | satisfied (S3-T2) |
| python: Logging | satisfied — module logger with the MR key on worker and dispatcher log lines (S2-T3, S3-T3) |
| TS-5 N+1 | satisfied — the API prefetches (S4-T6); booking loads candidates with their ownerships in one query (S4-T2) |
| TS-6 long-running job | satisfied — `run_worker` loops with no manual reconnect logic; failures raise |
| TS-1, TS-2, TS-4, TS-7 | not applicable — new database, no replicas, no Helm |
| TS-3 retention | at risk — inbox and audit grow without bound → out of scope for the prototype, recorded in Risks |
| implementation: Tests (DAMP, given/when/then, one per criterion) | satisfied — every test task names its criterion |

Completeness: models and migrations (S1–S4) · service logic (S2–S4) · endpoints (S1, S4) · error handling (S3 outbox, S4 unbooked) · concurrency (S2-T3, S4-T2) · validation (S1 token and UUID; S4 author ≠ reviewer) · tests (one per criterion) · feature flags N/A · logging (S2, S3) · metrics N/A for the prototype · rollback (drop the database).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| RMO-5 test is flaky (thread timing) | Medium | Low | Use `TransactionTestCase` with two threads and a barrier, and assert on counts, not timing |
| Inbox and audit tables grow without bound (TS-3) | High in production | Low in prototype | Out of scope; write it down for the production plan |
| Audit append-only enforced only in the ORM; raw SQL can still change rows | Low | Medium | Accepted for the prototype; production adds a DB trigger or a revoked UPDATE grant |
| Weekday default (Sat–Wed) is wrong for the company | Medium | Low | One setting, `RMO_DEFAULT_WORKING_DAYS` |
| Lease expiry re-runs a call that actually succeeded (GitLab set reviewer twice) | Low | Low | Outbox calls are idempotent by kind (set reviewer, notify); `resolve_areas` is guarded by "no area reviews yet" |
