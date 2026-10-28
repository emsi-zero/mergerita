# SPEC: Developer panel

Key: DVP · Route: project · Risk: high
Created: 2026-10-28

## Problem

Developers have no view into the orchestrator. Today the Django admin is the only UI, and it is for admins. An author cannot see:
- where their MR is,
- why it waits,
- who is booked to review it, and why.

A reviewer cannot see their bookings, and nobody can set their own working days, budget or leave.

The approved mockup (https://claude.ai/artifact/51t4UyQzgwT7ct2bLRZBLQ, in Tabdeal colors) defines three tabs: My MRs, My reviews, and Availability. This change builds them on today's backend.

## Non-goals

- No review-outcome actions (done / more time / hand back, flow 3) and no urgent request (flow 5). Those buttons are left out.
- No GitLab single sign-on. Login is a Django user account linked to a Person. SSO replaces the login page later.
- No handover of bookings that fall inside new leave (flow 4). Adding leave affects only new bookings.
- No team-wide queue, no reports, and no other person's data.
- No single-page app or JavaScript build. Pages are server-rendered Django templates with one CSS file.
- No Persian or RTL layout in this version.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| DVP-1 | IF a visitor is not signed in, THEN every panel page SHALL redirect to the login page. | SECURITY BOUNDARY | Anyone on the network reads MR and booking data |
| DVP-2 | IF a signed-in user is not linked to a Person, THEN every panel page SHALL reply 403 with a page that says the account is not linked yet. | SECURITY BOUNDARY | A user with no Person crashes the panel or sees someone else's data |
| DVP-3 | WHEN a developer opens My MRs, THE panel SHALL list only the MRs whose author is their Person, each with its title, state label and a one-line summary. | SECURITY BOUNDARY | Developers see each other's MRs, or miss their own |
| DVP-4 | WHEN a developer opens one of their MRs, THE panel SHALL show each area review with its areas, reviewer, booked day and stored reason, a next-step text for the MR's state, and the MR's history from its audit events. IF the MR is not theirs, THEN it SHALL reply 404. | CRITICAL BEHAVIOR | The author cannot tell why the MR waits, or reads another person's MR by changing the id |
| DVP-5 | WHEN an MR's changed lines exceed the size limit of any area it touches, THE MR page SHALL show a size warning. | BUSINESS RULE | Authors never learn that size delays their booking |
| DVP-6 | WHEN a developer opens My reviews, THE panel SHALL list the area reviews booked to them from today up to the booking horizon, grouped by day, with each day's booked minutes and budget. | CRITICAL BEHAVIOR | A reviewer misses a booking, or sees bookings of others |
| DVP-7 | WHEN a developer saves working days, THE panel SHALL store them on their Person; IF no day is selected, THEN it SHALL reject the change and show an error. | BUSINESS RULE | A person with no working days silently disappears from booking |
| DVP-8 | WHEN a developer saves a daily budget, THE panel SHALL accept only a value from `RMO_DAILY_BUDGET_CHOICES` and reject any other with an error. | BUSINESS RULE | A posted 0 or 10000 breaks capacity planning |
| DVP-9 | WHEN a developer adds leave, THE panel SHALL store it for their own Person; IF the end is before the start, THEN it SHALL reject it. A developer SHALL delete only their own leave, and another person's leave id SHALL reply 404. | SECURITY BOUNDARY | Someone removes a colleague's leave, or stores an impossible range |
| DVP-10 | WHEN a developer changes working days, budget or leave, THE panel SHALL write one audit event with the actor, the action, and the before and after values. | CRITICAL BEHAVIOR | Availability changes that move bookings cannot be traced |

Not tested, and why:
- The visual match with the mockup — checked by eye
- CSRF on POST forms — FRAMEWORK BEHAVIOR (Django middleware, already on)
- Login form and password handling — FRAMEWORK BEHAVIOR (`django.contrib.auth.views.LoginView`)

## Invariants

- INV-1 — A developer reads and changes only their own data — owned by the panel views — enforced by filtering every query on the signed-in user's Person, never on an id taken from the request alone.
- INV-2 — Every availability change and its audit event commit together — owned by the availability service — enforced by one `transaction.atomic` per change.
- INV-3 — Panel pages are read-only for MR, area review, booking and capacity data — owned by the panel views — enforced by having no POST handler that touches those models.

## Contracts

- New pages (GET):
  - `/panel/` → redirects to My MRs
  - `/panel/mrs/`
  - `/panel/mrs/<id>/`
  - `/panel/reviews/`
  - `/panel/availability/`
- New form posts:
  - `/panel/availability/days/`
  - `/panel/availability/budget/`
  - `/panel/availability/leave/`
  - `/panel/availability/leave/<id>/delete/`
- Login and logout: `/accounts/login/`, `/accounts/logout/` (Django auth views).
- New model field: `Person.user` (one-to-one to the Django user, nullable, unique). Admins link it in the admin.
- New settings: `RMO_DAILY_BUDGET_CHOICES = [60, 120, 180, 240]`, `LOGIN_URL`, `LOGIN_REDIRECT_URL`.

## Validation decisions

- Signed in, and linked to a Person — REQUIRED — checked in one view mixin (DVP-1, DVP-2).
- At least one working day; values 0–6 — REQUIRED — the form (DVP-7).
- Budget in `RMO_DAILY_BUDGET_CHOICES` — REQUIRED — the form (DVP-8).
- Leave: end ≥ start — REQUIRED — the form (DVP-9). Overlapping leave is allowed; booking treats it as a union.
- Leave in the past — NOT ADDED; harmless.

## Standards that apply

- python_code_style: Naming, Type annotations, Modular design, Constants and module globals
- django_standards: Configuration and business constants, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- technical_standards: TS-5 (N+1)
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| Build order | Panel first / GitLab first | Panel first; GitLab spec parked (developer) |
| Sign-in | Django login / GitLab OIDC | Django login linked to Person (developer) |
| Flow 3 and 5 actions | Include / leave out | Left out (developer) |
| Rendering | Server templates / SPA | Server-rendered templates; the design's SPA is deferred until it earns a build step |
| Who links users to People | Admin / self-service | Admin, in the Django admin |
| Next-step texts | — | One short text per MR state, written in the template, taken from the mockup |
