# DESIGN: Daily rebooking and stale-MR watchdog

Spec: spec.md · Key: RBW

## Shape

There are two new service modules, one per concept, both shaped like
`reviews/merge_retry.py`:

- `reviews/rebooking.py`: the daily claim, `retry_unbooked()` and `_rebook_one()`.
- `reviews/watchdog.py`: the daily claim, the case queries, the summary and
  `working_days()`.

`booking.py` gains only an `alert_on_no_fit` flag and the `unbooked_since`
writes. `MergeRequest.save()` owns `state_changed_at`. The worker command, the
admin action and the settings admin stay thin: they call the services.

## Write paths and locks

| Command (module.function) | Locks, in order | GitLab read before? | Audit action | Outbox jobs |
|---|---|---|---|---|
| `rebooking.run_daily_rebooking` (claim) | `RebookingSettings` row `select_for_update` (no other lock in this transaction) | none | — | — |
| `rebooking._rebook_one(area_review_id)` | MR row (`of=self`) → re-check the MR state in a new statement → `lock_booking()` → AreaReview row → re-check `state == unbooked` → CapacityDay rows (inside `book_area_review`) | none | `area_review.booked` (existing); `mr.in_review` when RBW-7 applies | `set_reviewer`, `notify` (existing, from `_book`) |
| `rebooking.retry_unbooked` (end of run) | none (own atomic block) | none | `booking.retry_run` | `notify` (summary, RBW-10) |
| `watchdog.run_daily_watchdog` (claim) | `RebookingSettings` row `select_for_update`; due only when `last_rebooking_on == today` | none | — | — |
| `watchdog` report (after the claim) | none: read-only queries, then one atomic block | none | `watchdog.run` | `notify` (admin summary), `notify` per missed-slot reviewer |
| `MergeRequest.save` | the caller's | — | — | — |

Each area review gets its own transaction (INV-2). The MR lock comes first,
because every other writer of an area review (`area_resolver`, `outcomes`,
`coverage`) holds the MR lock or `lock_booking()` first (LOCK-1).

`_rebook_one` reads the area review's MR id with a plain query before the
transaction, then locks the MR by that id.

### `_rebook_one`, under the locks

1. Read the sessions of the area review.
2. `excluded` = the reviewer id of every `handed_back` session (INV-3). The
   author is barred inside `book_area_review` already.
3. `preferred` = the reviewer of the latest session (by `created_at`, then
   `pk`), unless that reviewer is in `excluded`.
4. `continuation` = the area review has at least one session.
5. When there is a `preferred` reviewer, call
   `book_area_review(only_reviewer=preferred, exclude=excluded, continuation=…, alert_on_no_fit=False)`.
   When there is none, or that call returns False, call
   `book_area_review(exclude=excluded, continuation=…, alert_on_no_fit=False)`.
6. When booked, the MR is `awaiting_booking`, and no area review of the MR is
   `unbooked`, set the MR to `in_review` and record `mr.in_review` with before
   `awaiting_booking`, as `area_resolver._resolve` does. No `check_coverage` is
   queued: `_resolve` queues none for a first booking either.

The other area reviews of the MR are stable while the MR lock is held, because
their writers hold it too.

## Decisions

| Decision | Owner | Invariant it protects | Alternative rejected |
|---|---|---|---|
| The day claim is on its own `RebookingSettings` row, with two fields | `reviews.models` | INV-1 | Fields on `MergeRoutingSettings`: they mix concepts and both retries would contend on one row |
| The watchdog is due only after `last_rebooking_on == today` | `reviews.watchdog` | RBW-12, RBW-17 | A separate time: an admin could set the watchdog before the rebooking |
| `alert_on_no_fit=False` on `book_area_review` | `reviews.booking` | RBW-10 | A wrapper that removes the queued alert: it would be fragile, and the outbox row would already exist |
| `state_changed_at` is stamped in `MergeRequest.save()` against `_loaded_state` (set in `from_db`) | `reviews.models` | INV-5 | Stamping at the 9 call sites: easy to forget at the tenth. A signal: hidden, and it still needs `update_fields` handling |
| The "No progress" catch-all excludes MRs that are listed under another case, or that have an unbooked area review | `reviews.watchdog` | RBW-16, RBW-17 | Listing them again: duplicate alerts |
| The watchdog cases are a list of `(label, finder)` pairs | `reviews.watchdog` | — | Hard-coded blocks. The list is the extension point for later cases (author reminders, escalation) |
| Working days: `working_days(start: date, today: date, weekdays) -> int` counts the days d with start < d ≤ today and d.weekday() in weekdays | `reviews.watchdog` | RBW-19, DATA-2 | Calendar days |

## Data and migrations

There is one migration, `reviews/0013_rebooking_and_watchdog.py`, which
depends on `reviews` 0012 and on the latest `audit` migration. It runs these
operations in order:

1. `AddField MergeRequest.state_changed_at` — `DateTimeField(default=timezone.now, db_default=Now())`.
2. `AddField AreaReview.unbooked_since` — `DateTimeField(null=True, blank=True)`.
   The model default is `timezone.now`, so a new unbooked area review gets it on
   creation. `_book()` sets it to None.
3. `CreateModel RebookingSettings`. Times and thresholds have `db_default`.
   The thresholds have `MinValueValidator(1)` and `MaxValueValidator(60)`.
   `last_*_on` are `editable=False`.
4. `RunPython(backfill, RunPython.noop)`.

`backfill(apps, schema_editor)` is a module-level function, so the RBW-21 test
can call it with the live app registry. It writes with queryset `update()`,
because historical models have no custom `save()`. It runs two updates:

- **MergeRequest:** set `state_changed_at` from a `Subquery` that selects the
  latest `AuditEvent.created_at` where `subject_type="reviews.MergeRequest"`,
  `subject_id=Cast(OuterRef("pk"), CharField())` and `after__has_key="state"`,
  with `Coalesce` to `opened_at`.
- **AreaReview:** only rows with `state="unbooked"`. Set `unbooked_since` from
  the latest `area_review.unbooked` or `area_review.created` event, else the
  MR's `opened_at`.

The tables hold hundreds of rows, so one update per table is enough. The
worker may keep running (Contracts).

## Failure modes

| Input or fault | Behaviour | Criterion or INV |
|---|---|---|
| `book_area_review` raises for one area review (for example, the INV-4 `ValueError`) | Its transaction rolls back. It is logged and counted as still unbooked, and the run goes on | RBW-9, IO-2 |
| The worker crashes after the rebooking claim | That day's rebooking is not repeated (at most once, as MRDR). The watchdog still runs at the next pass, because the claim is set | INV-1, RBW-12 |
| Two workers at the run time | The second one waits on the settings row lock, then sees the claim and returns | RBW-3, RBW-12 |
| The MR changes between selection and the lock | It is checked again under the lock, then skipped | RBW-8 |
| A watchdog query fails | No claim is lost for the rebooking. The watchdog claim is already taken, so that day's report is lost. The exception reaches the worker loop, as `merge_retry` does. This is accepted: the next day reports again | INV-1 |
| The settings row is missing | `load()` creates it with the defaults | — |
| An admin sets a threshold of 0 or 61 | The admin form rejects it | RBW-22 |
| No admin Person exists | `queue_admin_alert` sends to an empty list (existing behaviour) | — |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Holding `lock_booking()` for every area review in one long run | Low (prototype scale) | Webhooks wait | Each area review gets its own short transaction, so the lock is released between items |
| Wrong backfill times flag many MRs on the first watchdog run | Medium | One noisy alert | RBW-21 test; the default stale threshold is 5 working days |
| A `state_changed_at` stamp missed by a queryset `update(state=…)` | Low | The watchdog is wrong for that MR | The grep shows no such update today. INVARIANTS gains a rule: MR state changes only through `save()` |
