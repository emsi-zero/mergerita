# TASKS: Orchestrator thin-slice prototype

Spec: spec.md · Key: RMO · Route: project · State: in progress
Checkpoint: `scripts/test.sh -q`

## Session 1 — Scaffold, webhook ingress, audit log

State: done · Covers: RMO-1, RMO-2, RMO-3, RMO-18

- [x] T1 — Add `requirements.txt` (Django 5.2, djangorestframework, psycopg[binary], pytest, pytest-django, black, isort, pylint, pylint-django), `Dockerfile`, `docker-compose.yml` (services `db` postgres:16 and `web`), `pytest.ini`, `pyproject.toml` (black, isort profile black), `.pylintrc`, `.gitignore`, `manage.py` — tooling for Gate A
- [x] T2 — Create the `orchestrator/` project: `settings.py` reads the environment only here (`DATABASE_URL` parts, `GITLAB_WEBHOOK_TOKEN`) and defines every `RMO_*` setting from the spec contracts, plus `RMO_OUTBOX_BACKOFF_SECONDS` and `RMO_DEFAULT_WORKING_DAYS`; `urls.py` — contracts
- [x] T3 — Add the `integrations` app with the `InboxEvent` model, its unique constraint on (`source`, `event_uuid`), the partial index on (`mr_key`, `id`), and its migration — RMO-2, RMO-3, INV-1
- [x] T4 — Add `integrations/inbox.py` `store_event(source, event_uuid, event_kind, mr_key, payload) -> bool` using an insert that ignores the unique conflict — RMO-2, RMO-3
- [x] T5 — Add the `reviews` app and `reviews/views.py` `GitLabWebhookView` at `POST /webhooks/gitlab/`: constant-time token compare → 401; missing `X-Gitlab-Event-UUID` → 400; derive `mr_key` from `project.id` and `object_attributes.iid` for merge-request events; call `store_event`; reply 202 — RMO-1, RMO-2, RMO-3
- [x] T6 — Add the `audit` app with `AuditEvent` (save refuses updates; `delete` and queryset `update`/`delete` raise), its migration, and `audit/services.py` `record(actor, action, subject, before, after)` — RMO-18, INV-6
- [x] T7 — Tests in `reviews/tests/test_webhook.py` for RMO-1, RMO-2, RMO-3, and in `audit/tests/test_audit.py` for RMO-18

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, security, query) · `deep-reviewer` (logic, security on INV-1, INV-6, token check)

Resume: Done. Run tests with `scripts/test.sh -q` (host .venv + postgres:14 over a unix socket in .pgsock/; set DEBUG=1 for manage.py and pylint). Docker files are kept but unbuilt (registry 403). `POST /webhooks/gitlab/` stores inbox rows; `audit.services.record` exists. Next: Session 2, the worker and MR lifecycle.

## Session 2 — Worker and MR lifecycle intake

State: pending · Covers: RMO-4, RMO-5, RMO-6, RMO-20

- [ ] T1 — Add `reviews/models.py` `Person`, `MergeRequest` with `MergeRequestState` `TextChoices`, and the migration — RMO-4
- [ ] T2 — Add `reviews/lifecycle.py` `handle_event(event)`: dispatch on `event_kind`; merge-request `open` for an unknown MR creates it in `awaiting_ai_review` (author looked up by GitLab user id) and calls `audit.services.record`; other kinds and unknown actions are no-ops. It opens no transaction of its own — RMO-4, INV-4
- [ ] T3 — Add `reviews/worker.py` `process_next_event() -> bool`: inside one `with transaction.atomic()`, claim the oldest pending event that is the minimum pending id for its `mr_key` (or has no `mr_key`) with `select_for_update(skip_locked=True)`, call `lifecycle.handle_event`, set `processed_at`. Log with the `mr_key` — RMO-5, RMO-6, INV-1
- [ ] T4 — Add `reviews/management/commands/run_worker.py`: loops `process_next_event` (and, from Session 3, the outbox dispatcher), sleeps when idle; `--once` drains and exits — contracts
- [ ] T5 — Tests in `reviews/tests/test_worker.py` for RMO-4, RMO-5 (`TransactionTestCase`, two threads), RMO-6
- [ ] T6 — In `reviews/views.py` `GitLabWebhookView`, reply 400 and store nothing when the body is not a JSON object, a Merge Request Hook lacks `project.id` or `object_attributes.iid`, or `X-Gitlab-Event-UUID` / `X-Gitlab-Event` / the derived `mr_key` exceed their field lengths; test RMO-20 in `reviews/tests/test_webhook.py` — RMO-20

Gate A: `bin/craft-spec trace --session 2` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic on INV-1, INV-4 and the claim query)

Resume: `run_worker --once` turns stored MR `open` events into `MergeRequest` rows. Next: Session 3, the outbox, adapters and area resolver.

## Session 3 — Outbox, adapters, AI gate and area resolver

State: pending · Covers: RMO-7, RMO-8, RMO-9, RMO-10, RMO-11, RMO-16

- [ ] T1 — Add `integrations/models.py` `OutboxCall` with `OutboxStatus` `TextChoices`, and the migration; add `reviews` models `CodeArea`, `Ownership` (`OwnerRole`), `AreaReview` (`AreaReviewState`) with M2M `areas`, and the migration — RMO-7, RMO-9, RMO-16
- [ ] T2 — Add `integrations/adapters.py` `GitLabGatewayInterface` (`get_changes`, `set_reviewer`, `post_note`) and `NotifierInterface` (`notify`) as `Protocol`s, a `get_gitlab_gateway()` / `get_notifier()` loader from settings dotted paths, and `integrations/fakes.py` in-memory fakes that record calls and return seeded changes — RMO-7, RMO-16
- [ ] T3 — Add `integrations/outbox.py`: `enqueue(kind, payload)` (called inside the caller's transaction), a handler registry, and `dispatch_due() -> int` that claims due rows with a lease in a short transaction, runs the handler outside any transaction, and records `done`, or retry with backoff, or `failed` at max attempts — RMO-16, INV-5
- [ ] T4 — Extend `reviews/lifecycle.py`: merge-request `update` events apply labels. Pass label in `awaiting_ai_review` → state `awaiting_booking`, audit, enqueue `resolve_areas`. Fail label → `waiting_on_author`, audit — RMO-7, RMO-17 (audit write; tested in S4)
- [ ] T5 — Add `reviews/area_resolver.py` and register handlers in `reviews/apps.py` `ready()`. The `resolve_areas` handler calls `get_changes` outside a transaction, then in one `atomic` block: locks the MR, returns if area reviews already exist, matches files to areas by glob, applies the policy (`strict`, or `combined` with fallback), creates `AreaReview` rows as `unbooked`, enqueues one size warning to the author when changed lines exceed any touched area's limit, and moves to `needs_triage` with an admin alert when a file matches no area. Also registers `set_reviewer` and `notify` handlers that call the adapters — RMO-7, RMO-8, RMO-9, RMO-10, RMO-11
- [ ] T6 — Wire `outbox.dispatch_due` into `run_worker`
- [ ] T7 — Tests in `reviews/tests/test_lifecycle.py` (RMO-7, RMO-8, RMO-11), `reviews/tests/test_area_resolver.py` (RMO-9, RMO-10), `integrations/tests/test_outbox.py` (RMO-16)

Gate A: `bin/craft-spec trace --session 3` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query, logic) · `deep-reviewer` (logic on INV-4, INV-5, the lease and the `resolve_areas` guard)

Resume: An MR with the pass label gets `AreaReview` rows (unbooked); the outbox retries failing calls. Next: Session 4, the booking engine and the read API.

## Session 4 — Booking engine, audit coverage, read API

State: pending · Covers: RMO-12, RMO-13, RMO-14, RMO-15, RMO-17, RMO-19

- [ ] T1 — Add `reviews` models `Leave` and `CapacityDay` (unique person+day), and the migration — RMO-12, INV-3
- [ ] T2 — Add `reviews/booking.py` `book_area_review(area_review)`: candidates are owners of all the review's areas, with the author excluded (and checked again before save), people on leave or not working excluded; one query with ownerships. For each day from today to the horizon, lock the candidates' `CapacityDay` rows, keep those with free minutes ≥ slot, rank by `(tier, -free, open_bookings, person_id)`, book the first. Set reviewer, day and reason; increment `booked_minutes`; record audit; enqueue `set_reviewer` and `notify`. With no fit, leave it `unbooked`, record audit, enqueue an admin alert. No transaction of its own — RMO-12, RMO-13, RMO-14, RMO-15, INV-2, INV-3
- [ ] T3 — Call `booking.book_area_review` for each new area review inside the `resolve_areas` transaction in `reviews/area_resolver.py`, and move the MR to `in_review` when every area review is booked — RMO-12
- [ ] T4 — Confirm every state change in `lifecycle`, `area_resolver`, `booking` calls `audit.services.record` — RMO-17, INV-4
- [ ] T5 — Register `Person`, `Leave`, `CodeArea`, `Ownership`, `MergeRequest`, `AreaReview`, `CapacityDay`, `InboxEvent`, `OutboxCall`, `AuditEvent` (read-only) in admin
- [ ] T6 — Add `reviews/serializers.py` `serialize_merge_request(mr) -> dict` and `MergeRequestDetailView` at `GET /api/merge-requests/<id>/` with `SessionAuthentication`, `IsAdminUser`, `select_related`/`prefetch_related` — RMO-19
- [ ] T7 — Tests in `reviews/tests/test_booking.py` (RMO-12, RMO-13, RMO-14, RMO-15), `reviews/tests/test_audit_trail.py` (RMO-17), `reviews/tests/test_api.py` (RMO-19)

Gate A: `bin/craft-spec trace --session 4` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query, security) · `deep-reviewer` (logic, security on INV-2, INV-3, the API permission)

Resume: The full thin slice works end to end with fakes. Next: `/finish`.
