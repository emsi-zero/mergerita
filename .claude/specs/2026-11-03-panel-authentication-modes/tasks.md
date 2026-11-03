# TASKS: Panel authentication modes

Spec: spec.md · Key: PAU · Route: project · State: done
Checkpoint: `scripts/test.sh -q`

## Session 1 — Separate panel sessions, local login, SSO stub

State: done · Covers: PAU-1, PAU-2, PAU-3, PAU-4, PAU-5, PAU-6

- [x] T1 — `orchestrator/settings.py`: `PANEL_AUTH_MODES` parsed from the environment (default `local,sso`), raising `ImproperlyConfigured` when empty or unknown. Add `PANEL_SESSION_COOKIE_NAME = "panel_sessionid"`, set `LOGIN_URL = "/panel/login/"`, and replace `SessionMiddleware` with `panel.middleware.AreaSessionMiddleware` — PAU-6, INV-1
- [x] T2 — `panel/middleware.py` `AreaSessionMiddleware`: choose the cookie name and path by the `/panel/` prefix in `process_request` and `process_response` — PAU-1, PAU-2, PAU-3
- [x] T3 — `panel/auth_views.py`:
  - `PanelLoginView` (GET form and GitLab button per mode; POST local sign-in only when `local` is on, else 404; safe `next` within `/panel/`)
  - `PanelLogoutView` (POST)
  - `SsoStartView` and `SsoCallbackView` (501 stub when `sso` is on, else 404; never sign in)

  — PAU-4, PAU-5, INV-2
- [x] T4 — `panel/urls.py`: `login/`, `logout/`, `sso/start/`, `sso/callback/`. In `orchestrator/urls.py`, remove `accounts/login/` and `accounts/logout/` — contracts
- [x] T5 — Templates: `panel/login.html` (Tabdeal style, the form and/or the GitLab button), `panel/sso_not_configured.html`, and the `base.html` sign-out form pointed at `panel:logout`. Remove `registration/login.html`
- [x] T6 — `panel/tests/helpers.py` `panel_login(client, user)`. Move the existing panel tests from `force_login` to it. The staff API tests keep `force_login`, which is the admin session
- [x] T7 — Tests in `panel/tests/test_auth.py`, one per criterion:
  - PAU-1: panel sign-in sets `panel_sessionid` with path `/panel/`; `/admin/` still redirects to the admin login, even for staff
  - PAU-2: an admin session only → panel redirects to `/panel/login/`
  - PAU-3: sign-out of each side leaves the other signed in
  - PAU-4: mode on and off, including POST → 404 and no session; plus a `next` subTest (`next=/admin/` → lands on `/panel/`)
  - PAU-5: the button, 501 on both stub URLs with no sign-in, 404 when off
  - PAU-6: the settings parsing rejects empty and unknown values (test the parsing helper)
- [x] T8 — `README.md` and `docs/testing.md`: the panel login URL is now `/panel/login/`; `PANEL_AUTH_MODES`; the SSO stub; admin and panel sign in separately

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style) · `deep-reviewer` (logic, security on INV-1, the middleware, the `next` redirect, the stub)

Resume: Session 1 done: panel session middleware, /panel/login/ and logout, SSO stub, tests, docs. All checks green. Next: Gate B review, then `/finish`.
