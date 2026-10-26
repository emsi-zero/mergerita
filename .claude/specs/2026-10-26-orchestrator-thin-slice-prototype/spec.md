# SPEC: Orchestrator thin-slice prototype

Key: RMO · Route: project · Risk: high
Created: 2026-10-26

## Problem

`docs/architecture-design.md` describes the Review & Merge Orchestrator. Its last
next step is a thin slice: webhook ingress, MR lifecycle, area resolver, and one
booking rule. Nothing has been built yet. So the core ideas are not proven in code:
the idempotent inbox, the outbox, explainable booking, and the audit log. This
prototype builds that slice in Django + DRF + PostgreSQL. All external systems are
fakes behind adapter interfaces.

## Non-goals

- No real GitLab, chat, calendar or directory client. Each is an adapter interface plus an in-memory or logging fake.
- No web app, no GitLab OpenID Connect sign-in. Django admin and a read-only API are the only UI.
- No review-session outcomes (done / more time / hand back), handover, continuity, urgency, author-wait, merge routing, release candidates, reconciliation, or watchdog (flows 3–9).
- No CODEOWNERS import. Code areas, ownership and people are entered in Django admin.
- No versioned rule-settings table. Rule values (slot length, daily budget, horizon, AI-review labels) are Django settings.
- No Celery or message broker. The worker is a management command that reads PostgreSQL.
- No readiness checks other than size (no CI-status or description check).
- No calendar-driven capacity. Capacity is the declared daily budget, minus booked slots, on the person's working days.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| RMO-1 | IF a GitLab webhook arrives with a missing or wrong `X-Gitlab-Token`, THEN THE webhook ingress SHALL reply 401 and store no inbox event. | SECURITY BOUNDARY | Anyone on the network can inject fake MR events |
| RMO-2 | WHEN a GitLab webhook arrives with a valid token, THE webhook ingress SHALL store one inbox event keyed by `X-Gitlab-Event-UUID` and reply 202 without processing it. | CRITICAL BEHAVIOR | Ingress does work inline, so a slow or failed step loses or delays the event |
| RMO-3 | WHEN the same event UUID is delivered twice, THE webhook ingress SHALL keep exactly one inbox event and reply 202 both times. | IMPORTANT FAILURE MODE | GitLab retries create duplicate MRs, bookings or notifications |
| RMO-4 | WHEN the worker processes a merge-request `open` event for an unknown MR, THE MR lifecycle SHALL create the MR record in state `awaiting_ai_review` and mark the inbox event processed. | CRITICAL BEHAVIOR | MRs never enter the pipeline |
| RMO-5 | WHEN two workers run at the same time over the same pending inbox events, THE worker SHALL process each event exactly once. | IMPORTANT FAILURE MODE | Two replicas double-book a reviewer |
| RMO-6 | WHILE an MR has two or more pending inbox events, THE worker SHALL apply them in the order they were received. | BUSINESS RULE | A later label change is overwritten by an earlier event |
| RMO-7 | WHEN an MR in `awaiting_ai_review` gets the configured AI-review pass label, THE MR lifecycle SHALL move it to `awaiting_booking` and create its area reviews; WHEN it gets the fail label, THE MR lifecycle SHALL move it to `waiting_on_author`. | BUSINESS RULE | The stage 1 gate is skipped, or a failed MR is booked |
| RMO-8 | WHEN an MR's changed lines exceed the size limit of any area it touches, THE MR lifecycle SHALL queue one size-warning notification to the author in the outbox. | BUSINESS RULE | Oversized MRs go in with no warning |
| RMO-9 | WHERE the coverage policy is `strict`, THE area resolver SHALL create one area review per touched code area. | BUSINESS RULE | A multi-area MR is approved by one owner of only one area |
| RMO-10 | WHERE the coverage policy is `combined` and one person is a code owner of every touched area, THE area resolver SHALL create one area review that covers all touched areas; otherwise it SHALL fall back to `strict`. | BUSINESS RULE | Combined review is given to someone who does not own all areas |
| RMO-11 | IF any changed file matches no code area, THEN THE MR lifecycle SHALL move the MR to `needs_triage` and queue an admin alert in the outbox. | IMPORTANT FAILURE MODE | An MR silently waits with no owner |
| RMO-12 | WHEN an area review is created, THE booking engine SHALL book a code owner of its area(s) who is not the author, not on leave, and working that day. It SHALL use the earliest day within the horizon with enough free budget for the slot. | BUSINESS RULE | The author reviews their own MR, or a person on leave is booked |
| RMO-13 | WHEN several candidates fit the same earliest day, THE booking engine SHALL prefer primary over backup owners, then more remaining budget that day, then fewer open bookings, then the lowest person id. | BUSINESS RULE | Booking is not deterministic, so its decisions cannot be explained or reproduced |
| RMO-14 | WHEN a booking is made, THE booking engine SHALL store a reason that names the owner tier, the day, and the free budget. It SHALL also queue two outbox calls in the same transaction: set the reviewer in GitLab, and notify the reviewer. | CRITICAL BEHAVIOR | Nobody can tell why an MR went where it did, or the booking is saved but GitLab is never told |
| RMO-15 | IF no candidate fits within the horizon, THEN THE booking engine SHALL leave the area review `unbooked` and queue an admin alert. | IMPORTANT FAILURE MODE | MR stuck with no owner and no alert |
| RMO-16 | IF an adapter call from the outbox raises, THEN THE outbox dispatcher SHALL keep the call pending, increment its attempts, and set a later next-attempt time. After the maximum attempts it SHALL mark the call `failed`. | IMPORTANT FAILURE MODE | A GitLab or chat outage loses actions, or retries forever |
| RMO-17 | WHEN an MR or area review changes state, or a booking is made, THE domain layer SHALL write one audit event with the actor, the action, the subject, and the before and after values. | CRITICAL BEHAVIOR | A state change cannot be audited |
| RMO-18 | IF code tries to update or delete an audit event, THEN THE audit model SHALL raise and leave the row unchanged. | SECURITY BOUNDARY | History is rewritten after the fact |
| RMO-19 | WHEN an authenticated staff user requests `GET /api/merge-requests/<id>/`, THE API SHALL return the MR state and each area review with its areas, reviewer, booked day and reason; IF the user is not authenticated, THEN it SHALL reply 401 or 403. | SECURITY BOUNDARY | Booking explanations are unreadable, or internal data leaks to anonymous callers |
| RMO-20 | IF a webhook with a valid token has a body that is not a JSON object, or a Merge Request Hook lacks `project.id` or `object_attributes.iid`, or a header value is longer than its stored field, THEN THE webhook ingress SHALL reply 400 and store no inbox event. | IMPORTANT FAILURE MODE | A malformed delivery gives a 500, and GitLab retries it forever |
| RMO-21 | IF handling an inbox event raises, THEN THE worker SHALL roll back that event's changes, mark the event failed with the error, queue an admin alert, and go on with other MRs' events; later events of the same MR SHALL stay pending until an admin clears the failed mark. | IMPORTANT FAILURE MODE | One bad event (unknown author, bad payload) crashes the worker on every pass or silently blocks its MR |

Not tested, and why:
- Django admin forms for people, areas, ownership, and clearing a failed inbox event's mark — FRAMEWORK BEHAVIOR
- Webhook events other than merge request (push, note, pipeline) are stored and marked processed with no effect — covered by RMO-2/RMO-4 paths; no business rule
- Fake adapters themselves — test scaffolding, not product behavior
- Docker Compose wiring — exercised by every checkpoint run

## Invariants

- INV-1 — An inbox event is applied at most once — owned by the worker — enforced by a unique constraint on (source, event UUID) and `SELECT … FOR UPDATE SKIP LOCKED` with processing and the `processed_at` mark in one transaction.
- INV-2 — The author is never booked as reviewer of their own MR — owned by the booking engine — enforced by the candidate filter, with a check before save.
- INV-3 — A person's booked minutes on a day never exceed their daily budget — owned by the booking engine — enforced by locking the person's capacity-day row while booking.
- INV-4 — Every state change and its audit event commit together — owned by the domain services — enforced by one `transaction.atomic` per command.
- INV-5 — Outbox calls commit in the same transaction as the state change that causes them — owned by the domain services — enforced by writing outbox rows inside the command's transaction; dispatch happens later, outside it.
- INV-6 — Audit events are append-only — owned by the audit model — enforced by `save` refusing updates and `delete` refusing.

## Contracts

- `POST /webhooks/gitlab/` — new. Accepts GitLab webhook JSON. Headers: `X-Gitlab-Token`, `X-Gitlab-Event`, `X-Gitlab-Event-UUID`. Returns 202, 400 (malformed body or headers, RMO-20; missing event UUID) or 401.
- `GET /api/merge-requests/<id>/` — new, read-only, staff only.
- Settings keys: `GITLAB_WEBHOOK_TOKEN` (from environment), `RMO_SLOT_MINUTES`, `RMO_DEFAULT_DAILY_BUDGET_MINUTES`, `RMO_BOOKING_HORIZON_DAYS`, `RMO_AI_PASS_LABEL`, `RMO_AI_FAIL_LABEL`, `RMO_COVERAGE_POLICY`, `RMO_OUTBOX_MAX_ATTEMPTS`.
- Management commands: `run_worker` (processes the inbox, then dispatches the outbox, in a loop or once with `--once`).

## Validation decisions

- Webhook token — REQUIRED — checked in the ingress view with a constant-time compare.
- Event UUID present — REQUIRED — ingress rejects with 400 when it is absent, since idempotency depends on it.
- Payload shape — REQUIRED only for the fields ingress reads (body is a JSON object; MR hook has `project.id`, `object_attributes.iid`) and header lengths (RMO-20). Nothing else is checked. Unknown event kinds are stored and ignored.
- Author ≠ reviewer — REQUIRED — booking engine (INV-2).

## Standards that apply

- python_code_style: Naming, Type annotations, Enums, Modular design, Object-oriented design, Logging, Constants and module globals
- django_standards: Configuration and business constants, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- technical_standards: TS-5 (N+1), TS-6 (long-running worker connection handling)
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| Stack | Django+DRF+Postgres / +Celery / FastAPI | Django + DRF + Postgres, worker as management command (developer) |
| Scope | Thin slice / + review loop / full | Thin slice (developer) |
| Integrations | Fakes / fakes + real GitLab | Interfaces + in-memory fakes (developer) |
| How does the AI Reviewer signal its verdict? | Label / pipeline job / bot approval | Assumed: a label on the MR, names in settings (`ai-review::passed`, `ai-review::failed`) |
| Where do changed files and line counts come from? | Webhook payload / GitLab API | Assumed: a GitLab gateway method `get_changes(project_id, iid)`; the fake returns seeded data |
| Slot length and daily budget | Fixed / by MR size | Assumed: fixed 60-minute slot, 120-minute default daily budget per person, overridable per person; horizon 10 calendar days |
| Working week | Mon–Fri / per person | Per person, default Saturday–Wednesday, following the design's "per person" rule; holidays not modelled |
| API auth | Staff session / token | Django session auth, staff only (OIDC is a non-goal) |
