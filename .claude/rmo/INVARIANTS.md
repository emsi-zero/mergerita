# Invariants — merge-orchestrator

The cross-cutting rules every change must keep. Specs cite these IDs under
"Invariants → Holds:", and the `logic`, `security` and `query` lenses check them.
Every rule here was either written in an approved spec (the source in brackets)
or was paid for by a review finding. Line numbers drift: grep the function name.

When a spec adds a rule that every later change must keep, add it here in the
same change, with its source.

## LOCK — locks and concurrency

- **LOCK-1 Lock order.** Every `reviews` writer takes locks in this order: MR row
  → `booking.lock_booking()` → AreaReview → ReviewSession → CapacityDay. Never
  the reverse, never a second `lock_booking()`. [RVO INV-6; `reviews/tests/test_lock_order.py`]
- **LOCK-2 Lock, then read again.** Eligibility and state are read in a new
  statement *after* the MR lock is held, never from a row loaded before it, and
  never in the same statement that takes the lock. [MRDR round-1 finding]
- **LOCK-3 Global booking lock.** Capacity, booking, coverage and merge routing
  run under `lock_booking()` (`pg_advisory_xact_lock`). `merge_router.assign_merge`
  expects its caller to hold the MR lock and the booking lock. [RMO INV-3, MRG INV-1]
- **LOCK-4 Session-level advisory locks** (`set_reviewer` per MR, `reconcile_all`
  per run) use `pg_try_advisory_lock` with no transaction open. Busy raises a
  retryable error such as `ReviewerLockBusy`. A lock wait must never spend the
  outbox lease. These locks are not compatible with PgBouncer transaction pooling, and that is documented.
- **LOCK-5 "One open X" is a partial unique constraint**, not only a check in
  code: one open merge assignment (`merge_assignment_one_open`), one active
  presence request, one pending session per area review. [MRG INV-5, MRV INV-1, RVO INV-1]
- **LOCK-6 Concurrency tests are mutation-checked.** A two-thread
  `TransactionTestCase` must fail when the lock is removed. Say in the test
  report that you checked this.

## TX — transactions

- **TX-1 One command, one `transaction.atomic`.** The state change, its audit
  event and its outbox rows commit together or not at all. [RMO INV-4, INV-5; RVO INV-5]
- **TX-2 No GitLab call inside a transaction.** Read GitLab first, then lock and
  write. `merge_confirmation.confirm_merge` is the reference shape. [GLI INV-1, RVO INV-3, MRG INV-4]
- **TX-3 Chat and alerts only through the outbox**: `alerts.queue_notification`
  and `alerts.queue_admin_alert`, inside the same transaction. Never send directly.

## IO — inbox, outbox, worker

- **IO-1 Inbox at most once.** `inbox.store_event` does `get_or_create` on
  (source, event_uuid). The worker claims the oldest pending event per `mr_key`
  with `skip_locked`, after the claim grace, and it handles the event and sets
  `processed_at` in one transaction. [RMO INV-1]
- **IO-2 Poison never blocks the system.** A malformed or unexpected input
  gives a 4xx or a quarantine with an admin alert. It never crash-loops, never
  quarantines an MR for good on a cosmetic field, and never stops a batch. A
  per-item exception in a batch (retry, reconcile) is caught per item.
  [RMO-20, RMO-21, MRV High, MRDR]
- **IO-3 Outbox lease fence.** A handler result is written only while the
  worker still holds the lease (`outbox._record`). Handlers are idempotent,
  because a retry runs them again. Handlers raise on failure so the backoff
  works (`30*2^n`, FAILED after 5 attempts). GitLab calls have a 45 s deadline,
  below the 60 s lease.
- **IO-4 Reconcile uses the inbox.** Reconciled MRs enter as `source=reconcile`
  events and never write MR rows directly. The cursor advances in the page's
  transaction. Webhooks cannot send `reconcile` or `merged` actions. [GLI INV-2, INV-4]
- **IO-5 New handlers are registered** in `reviews/apps.py` with
  `outbox.register()`, and the spec lists them under Contracts.

## GL — GitLab is the source of truth

- **GL-1 Hooks are triggers, not facts.** Terminal state, merger, verdicts and
  approvals are read from the GitLab API, never from the hook payload. The
  merger is `merge_user`, then `merged_by`, and an alert is sent when both are
  missing. [MRG INV-3, MRG-12]
- **GL-2 Fresh approval.** A verdict counts only when it is newer than
  `max(ai_gate_entered_at, head_sha_at)` (`coverage.is_fresh`). An approval
  given before a push, a revoked approval, or an old bot verdict after a push
  never counts. [RVO INV-2] This class produced the most Highs. Any change near
  `coverage`, `ai_gate` or `outcomes` gets a freshness criterion.
- **GL-3 The orchestrator never merges.** `GitLabGatewayInterface` has no merge
  method. Do not add one.
- **GL-4 Stale snapshots do not roll back newer state.** A reconcile or late
  event never overwrites a newer `head_sha`, `web_url` or state. A rare window
  that heals at the next reconcile is accepted (see reviewer-memory).
- **GL-5 The token comes only from the environment** and is never logged.
  Webhook auth uses `hmac.compare_digest`, fails closed when
  `GITLAB_WEBHOOK_TOKEN` is empty, and gives 400 on a malformed body. [GLI INV-3]

## SEP — separation of duties

- **SEP-1** The author is never booked as a reviewer of their own MR. A
  hand-back never books the same reviewer again. [RMO INV-2, RVO INV-4]
- **SEP-2** A merge owner is never the author or a current reviewer of the
  MR. [MRG INV-2]

## AUTH — panel and admin

- **AUTH-1 Every panel query is scoped to `self.person`** (`PanelAccessMixin`).
  A scoping test has a decoy row that belongs to someone else. [DVP INV-1, PDA INV-1]
- **AUTH-2 Lookups by id also filter on ownership**, for example id AND
  reviewer AND booked. [MRV INV-3]
- **AUTH-3 One auth context per URL prefix.** The panel uses `panel_sessionid`
  and the admin uses `sessionid`. Neither opens the other. `next=` cannot leave
  the prefix. [PAU INV-1]
- **AUTH-4 The panel is read-only for MR and booking data.** It writes only
  through `reviews` services: presence, outcomes, availability. [DVP INV-3]
- **AUTH-5 Every POST endpoint has an anonymous test and an unlinked-user test.**
- **AUTH-6 The SSO stub never calls `login()`.** [PAU INV-2]

## DATA — data and time

- **DATA-1 Audit is append-only.** `audit.services.record(actor, action,
  subject, before, after)` runs inside the caller's transaction, with `before`
  read under the lock. Every state change by a person or a job writes one event. [RMO INV-6]
- **DATA-2 Time is Asia/Tehran and aware.** Use `timezone.now()`,
  `timezone.localdate()` and `localtime()`, and never a naive datetime or
  `date.today()`. "Today" and merge windows are local. Time logic gets a
  midnight probe in its tests.
- **DATA-3 Migrations.** Add new non-null fields with `db_default`. A data
  migration is `RunPython` with a noop reverse, and the spec says whether the
  worker must be stopped. `makemigrations --check` is part of Gate A.
- **DATA-4 Stored URLs from GitLab** are http(s), at most 500 characters, and
  the check never raises. [MRV INV-4]
- **DATA-5 MR state changes only through `MergeRequest.save()`,** never queryset
  `update(state=...)`, so `state_changed_at` stays true. [RBW INV-5]

## Docs

- Each feature adds or extends a lettered scenario in `docs/testing.md` §4. A
  scenario that was not run by hand is marked "(unverified)". The existing
  scenarios are A–Q (O = merge routing, O.8–9 = retry, P = daily rebooking, Q = watchdog), so the next new one is **R**.
- A new GitLab field or assumption adds a smoke-test item in `docs/testing.md` §6 (1–17 so far).
- A behaviour an admin or developer sees gets a README section. Architecture
  changes go into `docs/architecture-design.md`.
