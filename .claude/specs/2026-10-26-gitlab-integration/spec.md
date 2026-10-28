# SPEC: GitLab integration

Key: GLI · Route: project · Risk: high
Created: 2026-10-26

## Problem

Every GitLab call in the prototype goes to an in-memory fake. Three gaps follow:

- The orchestrator cannot read a real MR's changed files.
- It cannot set reviewers in GitLab.
- It cannot recover from a lost webhook.

The AI gate reads a label that the AI Reviewer never sets. The real AI Reviewer is a GitLab user: authors or GitLab assign it, and it comments and approves. Its approval is required but is not enough for the branch rules. Target: self-managed GitLab EE, Premium, 17.x or later.

## Non-goals

- The orchestrator does not assign the AI Reviewer user. Authors or GitLab already do.
- No merge, approval or branch-rule changes in GitLab. GitLab's approval rule stays the real merge gate.
- No `post_note` implementation. It has no caller yet; the real gateway raises `NotImplementedError` for it.
- No real chat, calendar or directory adapter.
- No GitLab Free fallback. Several reviewers on one MR need Premium.
- No webhook source-IP allow-list. The secret token stays the only check.
- No review-session outcomes, merge routing or release (flows 3, 7, 8).
- No contract test against a live GitLab. Tests use recorded payload shapes from the GitLab REST v4 docs. The first run at work is a manual smoke test.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| GLI-1 | WHEN `get_changes` is called, THE GitLab gateway SHALL return every changed file path from the MR diffs (both old and new path for a rename), and the added plus removed line count, across all result pages. | INTEGRATION CONTRACT | Files on page 2 are missed, so an area review is never created; renamed files escape their area |
| GLI-2 | IF GitLab marks any file diff as too large or collapsed, THEN THE area resolver SHALL treat the MR as over the size limit and queue the size warning. | IMPORTANT FAILURE MODE | A huge MR has a partial line count and gets no warning |
| GLI-3 | WHEN `set_reviewer` is called, THE GitLab gateway SHALL send the MR's current reviewer ids plus the new one, so existing reviewers (including the AI Reviewer) are kept; IF the person is already a reviewer, THEN it SHALL send no update. | INTEGRATION CONTRACT | Booking a human removes the AI Reviewer or another area's reviewer from the MR |
| GLI-4 | IF GitLab answers with a non-2xx status or does not answer within the timeout, THEN THE GitLab gateway SHALL raise an error, so the outbox retries the call; the access token SHALL never appear in the error or in logs. | IMPORTANT FAILURE MODE | A failed call counts as done, or the token leaks into `last_error` |
| GLI-5 | WHEN a Merge Request Hook or a Note Hook arrives for an MR in `awaiting_ai_review`, THE MR lifecycle SHALL queue one AI-review check for that MR. | CRITICAL BEHAVIOR | The verdict is never read, so the MR waits forever |
| GLI-6 | WHEN the AI-review check runs, THE MR lifecycle SHALL read the AI Reviewer user's review state from GitLab. `approved` moves the MR to `awaiting_booking` and queues area resolution. `requested_changes` or `reviewed` moves it to `waiting_on_author` and notifies the author. Any other state leaves the MR unchanged. | BUSINESS RULE | An MR is booked without the AI approval, or a failed MR waits silently |
| GLI-7 | WHEN a Merge Request Hook reports new commits on an MR in `waiting_on_author`, THE MR lifecycle SHALL move it back to `awaiting_ai_review`. | BUSINESS RULE | A fixed MR never returns to the AI gate |
| GLI-8 | WHEN reconciliation runs, THE reconciler SHALL list each configured project's open MRs updated since its stored cursor, store one inbox event per MR and `updated_at` (idempotent by that pair), and only then advance the cursor. | IMPORTANT FAILURE MODE | A lost webhook leaves an MR unknown or stuck |
| GLI-9 | WHEN reconciliation finds an open MR the orchestrator does not know, THE MR lifecycle SHALL create it exactly as for an `open` webhook. | CRITICAL BEHAVIOR | MRs opened during an ingress outage never enter the pipeline |
| GLI-10 | WHILE one worker is running reconciliation, THE reconciler SHALL skip the run in every other worker. | IMPORTANT FAILURE MODE | Two replicas both page through GitLab and race on the cursor |
| GLI-11 | IF a GitLab call fails during reconciliation, THEN THE reconciler SHALL keep that project's cursor unchanged, log the error, and let the worker go on. | IMPORTANT FAILURE MODE | An outage advances the cursor past MRs never read, or crashes the worker |

Not tested, and why:
- httpx connection pooling and TLS — FRAMEWORK BEHAVIOR
- The settings loader picking the real gateway by dotted path — covered since the prototype (RMO contracts); one line
- `post_note` raising `NotImplementedError` — no caller

## Invariants

- INV-1 — No GitLab call runs inside a database transaction — owned by the gateway callers — enforced by calling the gateway only from outbox handlers and the reconciler, before or after their `atomic` blocks.
- INV-2 — Reconciliation never moves a cursor past an MR it failed to store — owned by the reconciler — enforced by advancing the cursor in the same transaction that stores that page's inbox events.
- INV-3 — The access token is read only from the environment and is never logged or stored — owned by settings and the gateway — enforced by reading `GITLAB_TOKEN` in `settings.py` only and by building errors from status code and URL path.
- INV-4 — Replayed and webhook events go through the same inbox and lifecycle — owned by the reconciler — enforced by storing reconciled MRs as inbox events, never writing MR rows directly.

## Contracts

- New settings: `GITLAB_URL`, `GITLAB_TOKEN` (environment), `RMO_GITLAB_TIMEOUT_SECONDS` (default 10, below `RMO_OUTBOX_LEASE_SECONDS`), `RMO_AI_REVIEWER_GITLAB_USER_ID`, `RMO_GITLAB_PROJECT_IDS`, `RMO_RECONCILE_INTERVAL_SECONDS` (default 300).
- Removed settings: `RMO_AI_PASS_LABEL`, `RMO_AI_FAIL_LABEL`. The label gate (prototype RMO-7) is replaced by GLI-5 and GLI-6.
- `RMO_GITLAB_GATEWAY` can name `integrations.gitlab.GitLabRestGateway`. The fake stays the default.
- New outbox kind `check_ai_review`.
- New inbox source `reconcile`.
- GitLab REST v4 endpoints used: MR diffs, MR reviewers (GET), MR update (PUT `reviewer_ids`), project MR list (`updated_after`, `state=opened`).
- GitLab webhooks the orchestrator now reads: Merge Request Hook (`oldrev` for new commits) and Note Hook (on MRs).

## Validation decisions

- `GITLAB_URL` and `GITLAB_TOKEN` present when the real gateway is configured — REQUIRED — checked when the gateway is built, with a clear error.
- `RMO_AI_REVIEWER_GITLAB_USER_ID` set — REQUIRED — checked when the AI-review check runs; a missing value raises, so the outbox retries and then fails visibly.
- GitLab response shape — NOT ADDED beyond the fields read. A missing field raises and the outbox retries.

## Standards that apply

- python_code_style: Naming, Type annotations, Object-oriented design, Logging, Constants and module globals
- django_standards: Configuration and business constants, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- technical_standards: TS-6 (long-running worker)
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| GitLab tier and version | Free / Premium / Ultimate | Premium, 17.x+ (developer) |
| AI verdict source | Label / pipeline job / bot user | Bot user's review state: `approved` passes; `requested_changes` or `reviewed` without approval fails (developer) |
| Who assigns the AI Reviewer | Orchestrator / authors or GitLab | Authors or GitLab (developer) |
| Reconciliation in scope | Yes / no | Yes (developer) |
| HTTP client | httpx+respx / requests / python-gitlab | httpx + respx (developer) |
| Reviewer state names | — | Assumed from GitLab 17 docs: `unreviewed`, `reviewed`, `requested_changes`, `approved`, `unapproved`. Verify on the real instance in the smoke test |
| Does GitLab send a webhook when the bot requests changes? | — | Not relied on. A Note Hook from the bot's comment, or the next reconciliation, triggers the check (GLI-5, GLI-8) |
| Reconciliation scope | Configured projects / whole group | Configured project ids (`RMO_GITLAB_PROJECT_IDS`) |
