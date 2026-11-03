# DESIGN: Panel authentication modes

Spec: spec.md · Key: PAU

## Shape

Django keeps one session per request, identified by one cookie. A small middleware in `panel`, `AreaSessionMiddleware`, replaces `django.contrib.sessions.middleware.SessionMiddleware`. It picks the cookie by URL prefix: `panel_sessionid` (path `/panel/`) for requests under `/panel/`, and Django's `sessionid` for everything else. Because `AuthenticationMiddleware` resolves `request.user` from `request.session`, the panel and the admin each see only their own sign-in (INV-1). `django.contrib.auth.login()` and `logout()` work unchanged, because they act on whichever session the request carries.

Panel sign-in, sign-out and the SSO stub are views in `panel/auth_views.py`, kept apart from the data views. The enabled modes come from one setting, which is parsed and validated in `settings.py` (PAU-6).

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| `AreaSessionMiddleware(SessionMiddleware)` overrides `process_request` and `process_response`, choosing `(cookie name, cookie path)` per request. `/panel/` gives `PANEL_SESSION_COOKIE_NAME = "panel_sessionid"` and path `/panel/`; anything else keeps the `SESSION_COOKIE_*` settings. The other cookie attributes (secure, httponly, samesite, age) come from the same settings | `panel.middleware` | INV-1, PAU-1, PAU-2, PAU-3 | A second Django project or host for the panel (heavy); a user flag that the admin checks (still one session, so sign-out is shared) |
| `PANEL_AUTH_MODES`: parsed from the environment in `settings.py` into a tuple, default `("local", "sso")`; an empty or unknown entry raises `ImproperlyConfigured` at import | `orchestrator.settings` | PAU-6 | Validating on the first request (the error shows up late) |
| `PanelLoginView`: GET renders `panel/login.html` with a form if `local` is on, and a GitLab button if `sso` is on. POST is allowed only when `local` is on (404 otherwise); it uses `AuthenticationForm`, then `login()`, then redirects to a safe `next` (allowed host plus a `/panel/` prefix) or `/panel/` | `panel.auth_views` | PAU-4, the `next` rule | `django.contrib.auth.views.LoginView` (its redirect allows any same-host URL, including `/admin/`) |
| `PanelLogoutView`: POST only, `logout()`, redirect to `/panel/login/` | `panel.auth_views` | PAU-3 | GET sign-out (CSRF) |
| SSO stub: `SsoStartView` and `SsoCallbackView`, GET only. They render `panel/sso_not_configured.html` with status 501 when `sso` is on, and 404 otherwise. They never call `login()` | `panel.auth_views` | INV-2, PAU-5 | A disabled button only (no URL to build on later) |
| `LOGIN_URL = "/panel/login/"`. `PanelAccessMixin` stays the same: an anonymous panel request redirects there through `LoginRequiredMixin` | `orchestrator.settings` | DVP-1, INV-3 | — |
| Tests sign in to the panel through a construction helper, `panel/tests/helpers.py` `panel_login(client, user)`. It builds a session with the session engine, logs the user in on it, and sets `client.cookies["panel_sessionid"]`. Django's `force_login` writes only `sessionid`, so after this change it signs in to the admin only | tests | — | Posting the login form in every test (slow, couples data tests to the login form) |

## Session binding and deployment

- A panel session carries the marker `_rmo_area = "panel"`, set by `mark_panel_session` after `login()`. `AreaSessionMiddleware.process_request` swaps in an empty session when the marker does not match the URL area, so a session key copied into the other area's cookie signs no one in. The stored session is not touched.
- The panel assumes a root deployment: the cookie path and `LOGIN_URL` are `/panel/` with no `SCRIPT_NAME` prefix.

## Data and migrations

None. Panel sessions live in the same `django_session` table. The cookie name separates them.

Rollback: restore `SessionMiddleware`, the `/accounts/` URLs and `LOGIN_URL`. Panel cookies left in browsers are then ignored.

## Standards review

| Standard | Verdict |
|---|---|
| django: Configuration and business constants | satisfied — modes and the cookie name in settings (T1) |
| django: thin views | satisfied — the auth views only validate, sign in, and redirect |
| django: i18n | at risk, accepted — English literals in the panel templates, as decided for the panel |
| python: Modular design | satisfied — the middleware and auth views live in `panel`, the app that owns the panel's sessions |
| Security (spec) | satisfied — one auth context per request; a safe `next`; POST-only sign-out; the stub cannot sign in |
| implementation: Tests | satisfied — one test per PAU criterion; the existing DVP tests move to `panel_login` |

Completeness:
- Models: N/A.
- Middleware: T2.
- Views and URLs: T3, T4.
- Templates: T5.
- Tests: T6, T7.
- Docs: T8.
- Rollback: above.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The overridden `process_response` drifts from Django's own (for example, a future Django changes cookie handling) | Low | Medium | Keep the override small: compute name and path, then follow Django 5.2's logic. A test asserts both cookies' names and paths |
| Existing panel tests break because `force_login` no longer signs in to the panel | Certain | Low | T6 moves them to `panel_login` |
| `sso` alone locks everyone out while it is a stub | Low | Medium | Allowed by spec; the README warns |
