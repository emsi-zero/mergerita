# TASKS: Developer panel

Spec: spec.md · Key: DVP · Route: project · State: in progress
Checkpoint: `scripts/test.sh -q`

## Session 1 — Sign-in, layout, My MRs

State: done · Covers: DVP-1, DVP-2, DVP-3, DVP-4, DVP-5

- [x] T1 — Add `Person.user` to `reviews/models.py` with its migration, and add `user` to the Person admin. In `orchestrator/settings.py`, add `panel` to `INSTALLED_APPS`, plus `RMO_DAILY_BUDGET_CHOICES`, `LOGIN_URL` and `LOGIN_REDIRECT_URL`. In `orchestrator/urls.py`, add `accounts/` (Django auth URLs) and `panel/` — DVP-1, DVP-2
- [x] T2 — Create the `panel` app. Add `panel/static/panel/panel.css` with the Tabdeal palette from the mockup (https://claude.ai/artifact/51t4UyQzgwT7ct2bLRZBLQ; colors in `.claude/design/devpanel/project/Main.dc.html`), `panel/templates/panel/base.html` (header, tabs, user, logout), `registration/login.html` and `panel/not_linked.html`
- [x] T3 — Add `PanelAccessMixin` to `panel/views.py`. It requires login, resolves `request.user.person`, and renders a 403 with `not_linked.html` when there is no Person — DVP-1, DVP-2, INV-1
- [x] T4 — Add `MyMergeRequestsView` (`/panel/mrs/`, and `/panel/` redirecting to it). List the person's MRs with a badge and summary per state, prefetching area reviews — DVP-3
- [x] T5 — Add `MergeRequestDetailPanelView` (`/panel/mrs/<id>/`). Scope it to the author, and show area reviews (areas, reviewer, day, reason), the next-step text, the size warning and the audit history — DVP-4, DVP-5
- [x] T6 — Add tests in `panel/tests/test_access.py` (DVP-1, DVP-2) and `panel/tests/test_merge_requests.py` (DVP-3, DVP-4, DVP-5)

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1, the mixin and the 404 scoping)

Resume: Done: sign-in, panel layout, My MRs list and detail, 5 tests. Tabs for My reviews and Availability are not in base.html yet (add in S2 T4). Login/logout live; unlinked page is 403. Next: Session 2, My reviews and Availability.

## Session 2 — My reviews and Availability

State: pending · Covers: DVP-6, DVP-7, DVP-8, DVP-9, DVP-10

- [ ] T1 — Add `reviews/availability.py` with `set_working_days`, `set_daily_budget`, `add_leave` and `delete_leave`. Each runs one `atomic` block and writes one audit event. `delete_leave` is scoped to the person — DVP-7, DVP-8, DVP-9, DVP-10, INV-2
- [ ] T2 — Add `panel/forms.py` with `WorkingDaysForm`, `BudgetForm` and `LeaveForm`. Add `AvailabilityView` (GET) to `panel/views.py`, and POST views for days, budget, leave add and leave delete (another person's leave → 404). Valid posts redirect with a message; invalid ones re-render with 400 — DVP-7, DVP-8, DVP-9
- [ ] T3 — Add `MyReviewsView` (`/panel/reviews/`). Show bookings from today through the horizon, grouped by day, with booked minutes and budget from one `CapacityDay` query — DVP-6
- [ ] T4 — Add templates `panel/reviews.html` and `panel/availability.html` in the mockup layout, with no outcome or urgent buttons
- [ ] T5 — Add tests in `panel/tests/test_reviews.py` (DVP-6) and `panel/tests/test_availability.py` (DVP-7, DVP-8, DVP-9, DVP-10)
- [ ] T6 — Add a "Developer panel" section to `docs/testing.md` (link a user to a Person in admin, sign in, what to check) and to `README.md`

Gate A: `bin/craft-spec trace --session 2` green · `bin/craft-spec trace` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1, INV-2 and the leave delete)

Resume: The panel is complete for this version. Next: `/finish`, then resume the parked GitLab spec.
