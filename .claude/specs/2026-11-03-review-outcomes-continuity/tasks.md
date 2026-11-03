# TASKS: Review outcomes and continuity

Spec: spec.md · Key: RVO · Route: project · State: in progress
Checkpoint: `scripts/test.sh -q`

## Session 1 — Review sessions, booking refactor, outcomes

State: done · Covers: RVO-1, RVO-2, RVO-3, RVO-4, RVO-5, RVO-9

- [x] T1 — `reviews/models.py`: add `ReviewSession` and its partial constraint, `AreaReviewState.APPROVED`, `MergeRequestState.APPROVED`, and outcome `superseded`. Write one migration that backfills a session for each booked area review — RVO-1, INV-1
- [x] T2 — `reviews/booking.py`: give `book_area_review` the parameters `only_reviewer`, `exclude`, `after_day` and `continuation`. It creates the session and updates the AreaReview cache. Add a helper that releases a session's minutes. The existing RMO-12..15 tests must pass unchanged — RVO-1, INV-4
- [x] T3 — `reviews/outcomes.py`: `mark_done`, `need_more_time` and `hand_back`, each in one atomic block with the scoped lookup, row locks, `lock_booking()` where they book, audit, and outbox (`set_reviewer` and `notify` for hand back; `notify` for more time; an admin alert when nothing fits) — RVO-2, RVO-3, RVO-4, RVO-5, INV-5
- [x] T4 — Panel:
  - Three POST views and URLs.
  - In `panel/reviews.html`, show the Done, Need more time and Hand back buttons on pending sessions and the outcome after.
  - Mark continuation sessions with a "Follow-up" chip and list them first in each day.
  - Style the new buttons with the existing `btn` classes.

  — RVO-5, RVO-9
- [x] T5 — Tests:
  - `reviews/tests/test_sessions.py` (RVO-1)
  - `panel/tests/test_outcomes.py` (RVO-2, RVO-3, RVO-4, RVO-5)
  - `panel/tests/test_reviews.py` (RVO-9)
  - Add the new POST URLs to the access tests.

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green · makemigrations --check clean
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1, INV-4, INV-5, capacity release, the scoped lookup)

Resume: Done (code written, Gate A green; Gate B not run). Bookings are sessions in `ReviewSession`; `book_area_review` takes `only_reviewer`, `exclude` (person ids), `after_day`, `continuation`; `reviews/outcomes.py` holds the three outcomes. Next: Session 2, coverage and follow-ups. Panel dicts (STATE_BADGES, STATE_SUMMARIES, NEXT_STEPS) have no `approved` MR state yet.

## Session 2 — Coverage check and follow-ups

State: pending · Covers: RVO-6, RVO-7, RVO-8

- [ ] T1 — `reviews/lifecycle.py`: hooks and reconcile events on an MR in `in_review` enqueue `check_coverage` — RVO-6
- [ ] T2 — `reviews/coverage.py` and its registration. Read verdict times, then states, outside the transaction. Under an MR lock, apply area approvals, the requested-changes move (notify the author), and the MR approval. Audit each change — RVO-7, INV-2, INV-3
- [ ] T3 — `reviews/area_resolver.py`, follow-ups. Read the reviewers' states outside the transaction. Then un-approve reset areas, supersede pending sessions (releasing minutes), book follow-ups with `only_reviewer` and `continuation=True`, and move the MR to `in_review` — RVO-8
- [ ] T4 — Mock GitLab: confirm that a human reviewer's `approve` and `request-changes` write the same system notes as for the bot (already the case per GLI-6). Optionally add a `reset-approvals` control for RVO-8 tests
- [ ] T5 — Tests:
  - `reviews/tests/test_coverage.py` (RVO-6, RVO-7)
  - `reviews/tests/test_followups.py` (RVO-8)
  - extend the `tests_e2e` GLI-12 flow with a subTest: the booked reviewers approve in the mock → the MR is `approved`
- [ ] T6 — `docs/testing.md`: an outcomes and approval scenario. README: the MR states now end at `approved`

Gate A: `bin/craft-spec trace --session 2` green · `bin/craft-spec trace` green · checkpoint green · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-2, INV-3, the stale-verdict rules, follow-up and reset handling)

Resume: An MR can be approved end to end. Next: `/finish`.
