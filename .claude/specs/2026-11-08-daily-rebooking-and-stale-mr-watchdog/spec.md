# SPEC: Daily rebooking and stale-MR watchdog

Key: RBW · Route: project · Risk: high
Created: 2026-11-08

Risk is high: the rebooking books under `lock_booking()` in a batch next to the
worker (LOCK, IO-2), both jobs claim a day under `select_for_update`, and a data
migration fills two new fields on existing rows (DATA-3).

## Problem

When `booking.book_area_review()` finds no reviewer within the horizon, the
area review becomes `unbooked`, one admin alert is sent, and nothing tries
again. This happens on the first booking, on a hand back, on need more time and
on a follow-up. So an MR can wait in `awaiting_booking` or `in_review` with no
reviewer, even after an owner comes back from leave, raises a budget, or is
added. Separately, nothing notices an MR that stopped moving: an AI Reviewer
that never answers, a booked day that passed with no outcome, or a `needs_triage`
MR that no admin handles. The rule is "nothing gets stuck: every MR has an owner
or raises an alert".

## Non-goals

- No automatic rebooking after a missed slot. The watchdog only reports and reminds.
- The watchdog changes no MR state, booking, session or merge assignment.
- No urgency, organisation minimum budget, continuation reserve, handover on
  leave or delegation, author reminders, or escalation to tech leads.
- No alert for `waiting_on_author` MRs, and no second alert for approved MRs
  without a merge owner (the merge retry summary covers them).
- No new GitLab read or write. Reviewer changes reach GitLab through the
  existing `set_reviewer` job (GL-3 holds).
- No new environment variables. Times and thresholds live in the database.
- No panel change.

## Acceptance criteria

### Part 1 — daily rebooking

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| RBW-1 | WHEN the worker runs at or after the daily run time (Asia/Tehran) and the day is not yet claimed for rebooking, THE rebooking SHALL claim the day and try to book every `unbooked` area review whose MR is `awaiting_booking` or `in_review`, using the normal booking rules (owner tier, free capacity, open bookings, leave, working days). It SHALL NOT try before the run time. | BUSINESS RULE | An unbooked area review stays without a reviewer after an owner becomes free |
| RBW-2 | WHILE rebooking, THE rebooking SHALL process the area reviews in order of `unbooked_since`, oldest first, so an older wait takes a free slot before a newer one. | BUSINESS RULE | A new MR takes the only free slot from one that has waited for a week |
| RBW-3 | WHEN two workers, or a restart, reach the run time on the same local day, THE rebooking SHALL run once only. WHEN the worker was down at the run time, it SHALL run at the first pass after it. | CONCURRENCY | Two runs on one day double-book, or a missed run day is never caught up |
| RBW-4 | WHEN the area review has an earlier session whose reviewer is still an eligible owner and did not hand it back, THE rebooking SHALL try that reviewer first, and book any other eligible owner only when that reviewer does not fit. The booking SHALL be a continuation when the area review has earlier sessions. | BUSINESS RULE | Continuity is lost: a new reviewer re-reads an MR the previous reviewer already knows |
| RBW-5 | IF a person handed back the area review in any earlier session, THEN THE rebooking SHALL never book that person on it. | REGRESSION | The retry undoes a hand back |
| RBW-6 | THE rebooking SHALL never book the MR author. | REGRESSION | An author is asked to review their own MR |
| RBW-7 | WHEN the rebooking books an area review of an MR in `awaiting_booking`, and every area review of that MR is now booked or approved, THE rebooking SHALL move the MR to `in_review` with the `mr.in_review` audit event. WHILE any area review of that MR stays unbooked, the MR SHALL stay `awaiting_booking`. | REGRESSION | An MR enters review with an area that nobody reviews, or never leaves `awaiting_booking` |
| RBW-8 | IF, between selection and the lock, the MR left `awaiting_booking` and `in_review` (merged, approved, waiting on author, needs triage, back to the AI gate) or the area review was booked by another path, THEN THE rebooking SHALL skip it and count it as skipped. | CONCURRENCY | The retry books a merged or approved MR, or books a second session |
| RBW-9 | IF booking one area review raises, THEN THE rebooking SHALL log it, count it as still unbooked, and go on with the next one. | IMPORTANT FAILURE MODE | One bad row stops the whole run every day |
| RBW-10 | WHEN a run ends, THE rebooking SHALL send no per-review "no reviewer fits" alert, SHALL send one admin alert that lists every area review still unbooked (project:iid, title, area names, whole days waited since `unbooked_since`) or no alert when none is left, and SHALL write one `booking.retry_run` audit event with the trigger (`daily` or `admin`) and the booked, still-unbooked and skipped counts. | BUSINESS RULE | Admins get one alert per review per day, or no sign that MRs are still stuck |
| RBW-11 | WHEN an admin runs "Retry booking" on selected MRs, THE admin action SHALL run the same rebooking at once for the unbooked area reviews of those MRs only, with trigger `admin`, and report the booked, still-unbooked and skipped counts. A selected MR with no unbooked area review counts as skipped, and the summary lists only the selected MRs' leftovers. | BUSINESS RULE | An admin who just added an owner must wait until tomorrow |
| RBW-23 | IF an MR is `awaiting_booking` and has a `resolve_areas` outbox job that is `pending` or `in_flight`, THEN THE rebooking SHALL skip its area reviews and count them as skipped, so the follow-up books them. | REGRESSION | The rebooking moves the MR to `in_review` first, and the pending follow-up then exits, so its follow-up bookings are lost |

### Part 2 — watchdog

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| RBW-12 | WHEN the worker runs at or after the daily run time, the day is claimed for rebooking and not yet claimed for the watchdog, THE watchdog SHALL claim the day and run once. Two workers on the same day SHALL run it once only. | CONCURRENCY | The watchdog reports before the rebooking ran, or twice a day |
| RBW-13 | WHILE an MR is `awaiting_ai_review` and more than the AI-review threshold of working days have passed since `ai_gate_entered_at`, THE watchdog SHALL report it under "No AI verdict". | BUSINESS RULE | An AI Reviewer that never answers blocks MRs silently |
| RBW-14 | WHILE a booked area review of an MR in `awaiting_booking` or `in_review` has a pending session whose day is before today, THE watchdog SHALL report it under "Missed slot", and SHALL send that session's reviewer a chat message asking them to mark it done, ask for more time, or hand it back, once per run. | BUSINESS RULE | A booked day passes and the review is never done or moved |
| RBW-15 | WHILE an MR is `needs_triage` and more than the triage threshold of working days have passed since `state_changed_at`, THE watchdog SHALL report it under "Needs triage". | BUSINESS RULE | A triage MR waits for an admin forever |
| RBW-16 | WHILE an MR that is not merged, not `waiting_on_author`, and not approved without an open merge assignment has had no state change for more than the stale threshold of working days, and is not reported under another case, THE watchdog SHALL report it under "No progress". | BUSINESS RULE | An MR stuck for a reason no case foresees goes unnoticed |
| RBW-17 | THE watchdog SHALL NOT report unbooked area reviews, `waiting_on_author` MRs, or approved MRs without an open merge assignment. The rebooking summary and the merge retry summary report them. | REGRESSION | Admins get the same MR twice a day from two jobs |
| RBW-18 | WHEN the watchdog finds at least one MR, THE watchdog SHALL send one admin alert grouped by case, one line per MR with project:iid, title, web_url when known, and whole working days stuck. It SHALL send no alert when it finds none, and SHALL write one `watchdog.run` audit event with the count per case. An MR still stuck the next day SHALL be reported again. | BUSINESS RULE | Alerts are spread over many messages, or a stuck MR drops out after one report |
| RBW-19 | THE watchdog SHALL count working days in Asia/Tehran: the company working days (`RMO_DEFAULT_WORKING_DAYS`) for MR cases, and the reviewer's own working days for a missed slot. A day counts when it is after the start date and on or before today. | BUSINESS RULE | A weekend or a UTC date boundary makes an MR look stuck, or hides it |

### Shared

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| RBW-20 | WHEN a MergeRequest's state changes, THE model SHALL set `state_changed_at` to now. A save that does not change the state SHALL leave it as it is. | CRITICAL BEHAVIOR | The watchdog misses a stuck MR, or flags an MR that just moved |
| RBW-21 | WHEN the migration runs, THE migration SHALL set `state_changed_at` of each existing MR to the time of its latest audit event with a state in `after`, or `opened_at` when it has none, and `unbooked_since` of each unbooked area review to the time of its latest `area_review.unbooked` or `area_review.created` audit event, or its MR's `opened_at`. | IMPORTANT FAILURE MODE | Every existing MR is reported as stuck on the first run, or none is |
| RBW-22 | IF an admin enters a threshold below 1 or above 60 working days, THEN THE settings admin SHALL reject it. The run time is a valid time of day. | BUSINESS RULE | A threshold of 0 alerts on every MR every day |

Not tested, and why:
- The `set_reviewer` job and the reviewer chat on a rebooking — existing
  `_book()` behaviour, already covered by the booking tests. RBW-1's test
  asserts the booking only.
- That the settings row has no add or delete in the admin — FRAMEWORK BEHAVIOR.

## Invariants

- Holds: LOCK-1, LOCK-2, LOCK-3, LOCK-5, LOCK-6, TX-1, TX-2, TX-3, IO-2, GL-3,
  SEP-1, DATA-1, DATA-2, DATA-3.
- INV-1 — the rebooking and the watchdog each run at most once per local day —
  owned by `reviews.rebooking` and `reviews.watchdog` — enforced by claiming
  `last_rebooking_on` and `last_watchdog_on` under `select_for_update` on the
  settings row before any work (the MRDR-2 shape).
- INV-2 — each area review is rebooked in its own transaction: MR row lock →
  `lock_booking()` → AreaReview lock → capacity rows, with eligibility read
  again after the MR lock — owned by `reviews.rebooking` — enforced by the
  `_rebook_one` shape of `merge_retry._route_one`.
- INV-3 — the rebooking never books a person with a `handed_back` session on
  that area review, nor the author — owned by `reviews.rebooking` — enforced by
  the `exclude` argument of `book_area_review`.
- INV-4 — the watchdog writes only its claim, its audit event and outbox rows —
  owned by `reviews.watchdog` — enforced by code shape (no save of MR, area
  review, session or assignment). The RBW-14 test asserts it.
- INV-5 — `state_changed_at` changes exactly when `state` changes — owned by
  `MergeRequest.save` — enforced by comparing with the state loaded from the
  database, and adding the field to `update_fields`.

## Contracts

- Migration `reviews` 0013: `MergeRequest.state_changed_at` (`db_default=Now()`),
  `AreaReview.unbooked_since` (nullable), the `RebookingSettings` model, and a
  data migration (RunPython with a noop reverse) that fills the two fields
  (RBW-21). The worker may keep running. A row changed while it runs gets a
  time that is at most seconds old.
- `RebookingSettings`, a single row, in the admin as "Rebooking and watchdog
  settings":
  - `daily_run_time`, default 07:30
  - `ai_review_threshold_days`, default 1
  - `triage_threshold_days`, default 1
  - `stale_threshold_days`, default 5
  - `last_rebooking_on` and `last_watchdog_on`, not editable
- `booking.book_area_review` gets `alert_on_no_fit: bool = True`. Existing
  callers do not change.
- `AreaReview.unbooked_since` is set on creation and by `_unbook()`, and
  cleared by `_book()`.
- Audit actions:
  - `booking.retry_run`: subject is the settings row, after `{trigger, booked, unbooked, skipped}`
  - `watchdog.run`: subject is the settings row, after `{no_ai_verdict, missed_slot, needs_triage, no_progress}`
  - existing: `area_review.booked`, `mr.in_review`
- Admin action on MergeRequest: "Retry booking" (`permissions=["change"]`).
- The worker loop calls `rebooking.run_daily_rebooking()`, then
  `watchdog.run_daily_watchdog()`, on every pass.
- Alert and chat texts:
  - Rebooking summary: `N area review(s) still have no reviewer:` followed by
    `<project>:<iid> <title> — <areas> — waiting <d> day(s)` lines.
  - Watchdog summary: `Stuck MRs (N):`, then one `<case> (n):` block per case
    with `<project>:<iid> <title> <web_url> — <d> working day(s)` lines.
  - Missed slot chat to the reviewer: `Your review of MR <project>:<iid> was
    booked on <day> and has no outcome. Mark it done, ask for more time, or
    hand it back in the panel.`

## Validation decisions

- Threshold range 1–60 working days — USEFUL — model validators, shown in the admin form.
- MR state eligibility is checked again under the MR lock — REQUIRED (INV-2) — `reviews.rebooking`.
- No check that the run time is before the merge retry time — the jobs are independent.

## Docs

- `docs/testing.md` §4:
  - scenario **P**, "Daily rebooking": an MR with no free owner, add one, run
    the worker with the run time in the past, see the booking and the summary;
    then the "Retry booking" admin action.
  - scenario **Q**, "Stale-MR watchdog": an MR left in `awaiting_ai_review`,
    a missed slot, and a triage MR, with the thresholds set to 1.
  - Both are marked "(unverified)" until someone runs them by hand.
- README, "Merge owners" section: a new "Rebooking and the watchdog" subsection
  covering the admin setup, the run time, the thresholds and what each alert means.
- `.claude/rmo/INVARIANTS.md`: next scenario letter R.

## Checkpoint

`.claude/rmo/bin/rmo gate-a` (tests `scripts/test.sh -q`, pylint, isort, black, makemigrations --check, trace)

## Open questions

| Question | Options | Decision |
|---|---|---|
| Where times and thresholds live | (a) new fields on `MergeRoutingSettings`; (b) one new `RebookingSettings` row for both jobs; (c) one row per job | (b): one row, one run time, two day claims. Neither job concerns merges, and one row keeps one place for the admin to set up |
| How the previous reviewer is known | (a) the latest `ReviewSession` of the area review; (b) a new `previous_reviewer` field set by `_unbook()` | (a): sessions already keep the reviewer and outcome after `_unbook()`, so no new field is needed. The preferred reviewer is the latest session's reviewer, unless that person handed it back. Everyone with a `handed_back` session is excluded |
| How "last progress" is stored | (a) `MergeRequest.state_changed_at`; (b) the latest AuditEvent per MR | (a): set in `MergeRequest.save()`, so all 9 state writes are covered. Existing rows are filled from the audit log once, in the migration (RBW-21) |
| Who reports unbooked area reviews | (a) the rebooking summary only; (b) the watchdog only; (c) both | (a): the watchdog runs after the rebooking and leaves them out (RBW-17), so each MR reaches the admins once a day |
| Does the watchdog share the rebooking's run time and claim? | (a) one time and one claim; (b) one time, two claims, with the watchdog after the rebooking; (c) two times | (b): a crash in the rebooking does not cost that day's watchdog, and the watchdog always sees the rebooking's results |
| "Oldest first" measured by | (a) the MR's `opened_at`; (b) the area review's `unbooked_since` | (b): it measures how long this booking has waited. A hand back on an old MR is not ahead of a first booking that has waited longer |
| Default thresholds | — | AI review 1, triage 1, no progress 5 working days. They can be changed in the admin |
