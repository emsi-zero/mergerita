# SPEC: Panel authentication modes

Key: PAU · Route: project · Risk: high
Created: 2026-11-03

## Problem

The developer panel and the Django admin share one login and one session. Signing in to `/accounts/login/` as a staff user also opens the admin, and signing in to the admin also opens the panel. The panel will later sign developers in through GitLab single sign-on, while the admin stays a local, staff-only tool, so the two must be separate now. The panel also needs a place for SSO, shown but not working yet.

## Non-goals

- No working SSO. No OAuth client, token exchange, GitLab call, or user creation from GitLab. The SSO pages are a stub.
- No separate user table. Panel and admin users stay in Django's user table; a panel user still needs a Person link (DVP-2).
- No change to the admin's own login (`/admin/login/`) or to who may use the admin (`is_staff`).
- No change to the staff API (`/api/merge-requests/<id>/`), which keeps using the admin session.
- No login throttling (already decided: trusted network until SSO, panel log 2026-10-28).
- No "remember me", password reset or self-registration.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| PAU-1 | WHEN a user signs in on the panel login page, THE panel SHALL keep that sign-in in a panel session cookie of its own, and the admin SHALL still ask that user to sign in, even when the user is staff. | SECURITY BOUNDARY | A panel sign-in, which SSO will later grant to every developer, opens the admin |
| PAU-2 | WHEN a user is signed in to the admin only, THE panel SHALL redirect that user to the panel login page. | SECURITY BOUNDARY | An admin session silently opens the panel as that user |
| PAU-3 | WHEN a user signs out of the panel, THE admin session of the same browser SHALL stay signed in, and signing out of the admin SHALL leave the panel session signed in. | CRITICAL BEHAVIOR | Signing out of one tool unexpectedly ends the other, or does not end the one asked |
| PAU-4 | WHERE `local` is in `PANEL_AUTH_MODES`, THE panel login page SHALL show the username and password form and accept a valid local user; WHERE it is not, THE page SHALL show no form, and a posted login SHALL reply 404 and sign no one in. | SECURITY BOUNDARY | A disabled password login can still be used |
| PAU-5 | WHERE `sso` is in `PANEL_AUTH_MODES`, THE panel login page SHALL show a "Sign in with GitLab" button; its start and callback URLs SHALL reply with a "GitLab sign-in is not set up yet" page linking back to the login page and SHALL sign no one in. WHERE `sso` is not enabled, both URLs SHALL reply 404. | SECURITY BOUNDARY | The SSO stub signs someone in, or exists while SSO is turned off |
| PAU-6 | IF `PANEL_AUTH_MODES` is empty or names an unknown mode, THEN THE application SHALL refuse to start with a clear configuration error. | IMPORTANT FAILURE MODE | A typo leaves the panel with no way to sign in, discovered only by users |

Not tested, and why:
- The look of the login page and the stub page — checked by eye
- CSRF on the login and logout forms — FRAMEWORK BEHAVIOR (Django middleware, already on)
- Password checking — FRAMEWORK BEHAVIOR (`django.contrib.auth.authenticate`)

## Invariants

- INV-1 — A request carries at most one authentication context: the panel session on panel URLs, the admin session everywhere else — owned by the session middleware — enforced by choosing the session cookie by URL prefix, so `request.user` is resolved from exactly one cookie.
- INV-2 — Nothing in the SSO stub calls `login()` — owned by the panel views — enforced by the stub views having no sign-in path, and tested (PAU-5).
- INV-3 — The panel's access rules are unchanged: signed in on the panel and linked to a Person (DVP-1, DVP-2, INV-1 of the panel spec) — owned by `PanelAccessMixin`.

## Contracts

- New panel URLs: `/panel/login/` (GET form, POST local sign-in), `/panel/logout/` (POST), `/panel/sso/start/`, `/panel/sso/callback/` (stub).
- Removed: `/accounts/login/` and `/accounts/logout/`. `LOGIN_URL` becomes `/panel/login/`.
- New session cookie `panel_sessionid`, path `/panel/`. The admin keeps `sessionid`.
- New setting `PANEL_AUTH_MODES`, read from the environment as a comma-separated list of `local` and `sso`; default `local,sso`.
- The panel header's sign-out goes to `/panel/logout/`.

## Validation decisions

- `PANEL_AUTH_MODES` non-empty and only known modes — REQUIRED — checked when settings load (PAU-6).
- Login form fields — framework form (`AuthenticationForm`), no extra checks.
- The `next` redirect after sign-in — REQUIRED to stay on the panel (`url_has_allowed_host_and_scheme` plus a `/panel/` prefix), so the panel login cannot bounce a user into the admin or off-site.

## Standards that apply

- python_code_style: Naming, Type annotations, Constants and module globals
- django_standards: Configuration and business constants, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| What "separate" means | One user table + separate sessions / separate user tables / separate pages only | Separate logins and sessions, one user table (developer) |
| How modes are chosen | List of enabled modes / one mode | `PANEL_AUTH_MODES` list, default `local,sso` (developer) |
| What the SSO stub does | Not-configured page / disabled button / dev-only fake | "Not set up yet" page on start and callback, no sign-in (developer) |
| Status code of the stub page | 200 / 501 | 501 Not Implemented, so monitoring never mistakes it for a working sign-in |
| Is `sso` alone allowed while it is a stub? | Yes / no | Yes: the page then shows only the GitLab button, and nobody can sign in to the panel. Allowed, because it is how SSO will run later; the README says so |
