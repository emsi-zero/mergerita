# DESIGN: Review outcomes and continuity

Spec: spec.md · Key: RVO

## Shape

- **`ReviewSession` holds every booking** of an area review. `AreaReview.reviewer`, `booked_day`, `slot_minutes` and `reason` stay as a cache of the latest session, written in the same transaction. Existing readers (My reviews, the MR page, the API, booking capacity) keep working, and the session history is new.
- **Booking (`reviews/booking.py`) gains one entry point for every kind of booking:** `book_area_review(area_review, *, only_reviewer=None, exclude=(), after_day=None, continuation=False)`.
  - It creates the session and updates the cache.
  - First bookings call it as today.
  - **Need more time** passes `only_reviewer` and `after_day=session.day`.
  - **Hand back** passes `exclude={old reviewer}`.
  - **Follow-ups** pass `only_reviewer=area_review.reviewer`.
  - Every kind except the first booking passes `continuation=True`.
- **A new service, `reviews/outcomes.py`, handles the three outcomes:** `mark_done`, `need_more_time` and `hand_back`. Each runs one atomic block: lock the session's area review and session, check ownership and that the session is pending, set the outcome, call booking where needed, then write audit and outbox.
- **Coverage is an outbox job, `reviews/coverage.py`**, shaped like `ai_gate.check_ai_review`:
  - First, outside any transaction, read each booked or approved area review's current reviewer: verdict time first, then state.
  - Then, in one atomic block, lock the MR. Proceed only while it is in `in_review`. Apply the area approvals, a requested-changes move, or the MR approval.
- **Follow-ups (RVO-8) live in `area_resolver.resolve_areas`.** When the MR already has area reviews, it reads each reviewer's GitLab state outside the transaction. Inside the transaction it un-approves any area review whose reviewer is no longer approved, supersedes pending sessions, and books follow-ups for every area review that isn't approved. Then it moves the MR to `in_review`.

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| `ReviewSession(area_review FK, reviewer FK, day, slot_minutes, reason, is_continuation bool db_default False, outcome TextChoices db_default "pending", outcome_at null, created_at)`. Partial `UniqueConstraint(area_review, condition=outcome="pending", name="session_one_pending")` | `reviews.models` | INV-1 | Columns on AreaReview only (no history, no per-session outcome) |
| Migration backfills one `pending` session per booked area review from its cached fields (`is_continuation=False`) | `reviews.migrations` | RVO-1 | Leaving old bookings without a session (outcome buttons would not appear) |
| Capacity: a session's minutes go into `CapacityDay.booked_minutes` when booked. `hand_back` and `superseded` subtract them when `session.day >= today`, under the same capacity row lock booking takes | `reviews.booking` | INV-3 (panel budget) | Recomputing capacity from sessions (bigger change) |
| `book_area_review` keeps today's candidate rules and ranking. `only_reviewer` narrows the candidates to that one person, if they remain eligible (owner, not the author, working, not on leave). `exclude` removes people. `after_day` moves the first day searched. `lock_booking()` stays the caller's job, as today | `reviews.booking` | INV-4 | Separate booking functions per kind (duplicated rules) |
| Outcome endpoints: `/panel/sessions/<id>/{done,more-time,hand-back}/`, POST only, behind `PanelAccessMixin`. The lookup is `ReviewSession` by id, `reviewer=self.person`, `outcome=pending`, `area_review.state=booked`, and the session is the area review's latest; anything else gives 404 | `panel.views` → `reviews.outcomes` | RVO-5 | — |
| Coverage freshness: an approval counts when `latest_verdict_time(reviewer) > mr.ai_gate_entered_at`. This is the same gateway method and rule as GLI-6 | `reviews.coverage` | INV-2 | Session-time freshness (a reviewer may approve before clicking Done) |
| Requested changes by a human reviewer: MR → `waiting_on_author`, notify the author, keep the reviewers. The existing GLI-7 push then reopens the AI gate, and the existing `check_ai_review` pass queues `resolve_areas`, which now books follow-ups | `reviews.coverage`, `reviews.area_resolver` | RVO-7, RVO-8 | A separate follow-up trigger |
| Lifecycle trigger (RVO-6): a Merge Request Hook, Note Hook or reconcile event on an MR in `in_review` enqueues `check_coverage` | `reviews.lifecycle` | RVO-6 | — |
| My reviews ordering: within a day, continuation sessions first, then the oldest MR (`opened_at`) | `panel.views` | RVO-9 | — |

## Data and migrations

One `reviews` migration:
- `CreateModel ReviewSession` with its partial constraint.
- `RunPython` backfill for booked area reviews, reversible as a no-op.
- The new state choices need no schema change, because they are CharField choices.

- The outcome lookup relies on INV-1: a pending session is always the latest session of its area review, so it does not check "latest" again.
- Run migration 0008 with the worker stopped. Old code books without a session, and such a booking would get no outcome buttons.

Rollback: reverse the migration. Area review caches still hold the latest booking.

## Sessions

- **S1:** sessions, booking refactor and outcomes (RVO-1, 2, 3, 4, 5, 9).
- **S2:** coverage check and follow-ups (RVO-6, 7, 8), plus the docs.

## Standards review

| Standard | Verdict |
|---|---|
| django: Transactions | satisfied — one atomic block per outcome or coverage action; GitLab reads happen outside it (INV-3) |
| django: thin views | satisfied — the views call `reviews.outcomes` |
| django: FK `DO_NOTHING` + `db_constraint=False` | satisfied |
| TS-5 N+1 | satisfied — My reviews prefetches sessions and orders in the query |
| Concurrency | satisfied — area review and capacity row locks, `lock_booking()` around bookings, the partial unique constraint |
| implementation: Tests | satisfied — one test per RVO criterion; an e2e extension in S2 (approve in the mock → MR approved) |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The booking refactor changes first-booking behaviour | Medium | High | RMO-12..15 tests stay green, unchanged |
| A human reviewer's system-note texts differ from the AI bot's | Low | Medium | Same GitLab note texts; already on the smoke checklist |
| Approvals reset on push silently leave areas `approved` | Medium | Medium | RVO-8 re-reads states and un-approves |
