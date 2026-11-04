# Lens: security — application security of this service

- **Ceiling:** Critical · **Min confidence:** 0.5
- **Reads:** the diff, `INVARIANTS.md` AUTH and GL-5, `panel/middleware.py` and
  `PanelAccessMixin` (`panel/views.py`) when a panel view changes, `reviews/views.py`
  when the webhook changes, and `reviewer-memory.md` (the login throttle is accepted).

## Checklist — answer every item

1. **Webhook (GL-5)** — the token is compared with `hmac.compare_digest`, fails
   closed when it is unset, and every malformed input gives a 4xx (never a 500,
   never a quarantine loop). Header sizes are bounded. Webhooks cannot inject
   `reconcile` or `merged` actions (IO-4).
2. **Hook trust (GL-1)** — nothing security- or state-relevant (merger, approver,
   state) is taken from the payload without a GitLab read.
3. **Panel auth (AUTH-3, AUTH-6)** — the new view uses `PanelAccessMixin` (or the
   panel login path). The panel and admin sessions stay separate. `next=` stays under
   `/panel/`. No Django `auth.urls` views are exposed. The SSO stub signs no one in.
4. **Object scoping (AUTH-1, AUTH-2)** — every queryset and every lookup by id is
   filtered on `self.person` and on the role (author, booked reviewer, assigned merge
   owner). Another person's id gives 404, not 403-with-data.
5. **Write surface (AUTH-4)** — POST only, CSRF on, and the write goes through a
   `reviews` service. The panel never edits MR or booking rows directly.
6. **Tests for the boundary (AUTH-5)** — anonymous, signed in but not linked to a
   Person, and someone else's object, with a decoy row present.
7. **Admin actions** — `permissions=["change"]` (or stricter), and they act only on the
   selected rows.
8. **Secrets** — the GitLab token and the webhook secret come only from the
   environment. They are never logged, never stored in audit `before`/`after`, and
   never rendered.
9. **Output** — GitLab-supplied strings (titles, `web_url`, names) are escaped in
   templates. Links are http(s) only (DATA-4). There is no `|safe` on GitLab data.
10. **Outbound** — the gateway calls only `GITLAB_URL`, with the deadline. No
    user-influenced host.
11. **Settings** — `DEBUG` stays off by default, `SECRET_KEY` stays required, and a
    new env var fails at start-up when it is invalid.
