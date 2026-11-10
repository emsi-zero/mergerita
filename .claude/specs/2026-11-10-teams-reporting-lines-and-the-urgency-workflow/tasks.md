# TASKS: Teams, reporting lines and the urgency workflow

Spec: spec.md · Key: URG · Route: project · State: pending
Checkpoint: `.claude/rmo/bin/rmo gate-a`

## Session 1 — Data model, teams and reporting lines

State: done
Covers: URG-1, URG-2, URG-3, URG-4

- [x] T1 — every model change of design.md "Data and migrations" in `reviews/models.py` (Team, Person.team and reports_to, UrgencyRequest with `urgency_one_active`, MergeRequest.urgent_at, ReviewSession.is_urgent and overage_minutes, MergeAssignment.over_limit), and migration `reviews/migrations/0014_teams_and_urgency.py` — URG-1
- [x] T2 — `Person.clean()` (no self, no cycle) and `Team.clean()` (the delegate is not the lead); the Team admin and the Person admin fields — URG-1, URG-2, URG-3
- [x] T3 — the team and "reports to" in the panel header (`panel/templates/panel/base.html`, context processor) — URG-4
- [x] T4 — tests in `reviews/tests/test_teams.py` (URG-1, 2, 3) and `panel/tests/test_header.py` (URG-4)

Gate A: `.claude/rmo/bin/rmo gate-a --session 1 --log "Session 1 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families DATA, AUTH)

Resume: Session 1 done: models and migration 0014 (all spec fields), Person.clean and Team.clean, Team admin, header; Gate A green. Start Session 2 at T1 (reviews/urgency.py).

## Session 2 — Urgency requests, approver resolution, the decision tab

State: done
Covers: URG-5, URG-6, URG-7, URG-8, URG-9, URG-10, URG-11, URG-12, URG-13, URG-26

- [x] T1 — `reviews/urgency.py`: `resolve_approver`, `request`, `withdraw`, `decline`, `approve` (decision, `urgent_at`, audit, notify and team-limit warning only; the booking effect comes in Session 3), `weekly_count` — URG-5..13
- [x] T2 — `RMO_URGENCY_REASON_MAX_LENGTH` in `orchestrator/settings.py` — URG-5
- [x] T3 — panel: the request and withdraw forms on My MRs, the "Urgency requests" tab (views, URLs, templates, and the tab shown only to deciders and admins) — URG-5, 7, 9, 10
- [x] T4 — tests in `reviews/tests/test_urgency.py` (URG-5..8, 11, 13; URG-6 and URG-12 as two-thread TransactionTestCases, checked by removing the MR lock and the status re-check) and `panel/tests/test_urgency_panel.py` (URG-9, URG-10, with anonymous, unlinked, author and someone-else cases)

Gate A: `.claude/rmo/bin/rmo gate-a --session 2 --log "Session 2 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, AUTH, SEP)

Resume: Session 2 done: reviews/urgency.py (resolve_approver, request, withdraw, decline, approve, weekly_count, decidable_requests), panel forms/views/tab, tests; Gate A green. approve calls the empty _apply_urgency(merge_request) hook: Session 3 T3 fills it (URG-14); approve already refuses an MR that is no longer open (URG-26), so the booking effect never runs for a merged MR. No rights → 404; a lost race or a closed MR → error message and redirect. Gate B green after a delta re-review.

## Session 3 — Urgent booking

State: pending
Covers: URG-14, URG-15, URG-16, URG-17, URG-18, URG-19, URG-20, URG-21, URG-22, URG-24, URG-25

- [ ] T1 — `booking.book_area_review(urgent=None)`: derive the flag, skip the budget check in `_pick_slot`, `overage_minutes` and `is_urgent` in `_book`, the URG-18 alert — URG-15, 16, 18, 19, 20
- [ ] T2 — `rebooking`: make `_book` public as `book_with_continuity`, and take urgent MRs' area reviews first — URG-21
- [ ] T3 — `urgency.approve`: the booking effect (supersede and release later-day sessions, `book_with_continuity` for each area review to move) inside the decision transaction — URG-14, 17
- [ ] T4 — My reviews: urgent sessions first and marked — URG-22
- [ ] T5 — tests in `reviews/tests/test_urgent_booking.py` (URG-14..21) and `panel/tests/test_reviews.py` (URG-22); URG-24 and URG-25 as two-thread TransactionTestCases, checked by removing the MR lock in approve and the `lock_booking()` ordering

Gate A: `.claude/rmo/bin/rmo gate-a --session 3 --log "Session 3 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, TX, SEP, IO)

Resume: —

## Session 4 — Urgent merge routing and docs

State: pending
Covers: URG-23

- [ ] T1 — `merge_router.assign_merge(urgent=None)`: derive the flag, ignore the daily merge limit, record `over_limit`; `urgency.approve` re-routes an approved MR's later-day assignment — URG-23
- [ ] T2 — test URG-23 in `reviews/tests/test_urgent_booking.py`
- [ ] T3 — docs: `docs/testing.md` scenario R, README "Teams and urgency", and INVARIANTS.md (SEP-3, AUTH-7, next letter S)

Gate A: `.claude/rmo/bin/rmo gate-a --session 4 --log "Session 4 Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families LOCK, SEP)

Resume: —
