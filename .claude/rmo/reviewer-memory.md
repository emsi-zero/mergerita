# Reviewer memory

Decisions the developer already made. Reviewers read this file before they
review. A `suppress` entry is not raised again, unless the finding shows that
the stated condition no longer holds. Add an entry when the developer rejects
a finding as "accepted" or "not a defect". Give its source (spec key, date).

### M-stale-snapshot-window
kind: suppress
applies-to: reviews/reconciler.py, reviews/coverage.py, reviews/lifecycle.py
rule: A rare stale-snapshot window that heals at the next reconcile is accepted. Still a defect: stale data that rolls back newer state for good (GL-4), or that books or approves wrongly.
source: GLI, MRV, MRG logs, 2026-11

### M-fail-closed-alert
kind: suppress
applies-to: reviews/merges.py, reviews/merge_confirmation.py
rule: A false `before_approval` alert when an approval lands between the verdict read and the MR lock is accepted, because it fails closed. Still a defect: a missed alert.
source: MRG session 2 round 2, 2026-11-04

### M-retry-at-most-once
kind: suppress
applies-to: reviews/merge_retry.py
rule: The daily retry runs at most once per local day, even after a crash mid-run. Still a defect: two runs on one day, or an exception that stops the whole run.
source: MRDR, 2026-11-04

### M-small-query-cost
kind: suppress
applies-to: panel/*, reviews/admin.py
rule: Correlated subqueries, one extra COUNT, and the panel header adding 3-4 queries are accepted at prototype scale. Still a defect: N+1 per row, or an unbounded queryset.
source: DVP, PNS logs

### M-coverage-global-lock
kind: suppress
applies-to: reviews/coverage.py
rule: Coverage takes the global booking lock on every run (LOCK-3). Do not flag it as contention.
source: RVO

### M-vary-cookie
kind: suppress
applies-to: panel/middleware.py
rule: `Vary: Cookie` on every response is accepted.
source: PAU

### M-no-login-throttle
kind: suppress
applies-to: panel/auth_views.py
rule: The panel password login has no attempt limit. It runs only inside the company network until SSO replaces it (README). Still a defect: anything that exposes the login outside that assumption, or a user enumeration leak.
source: PAU, README

### M-pgbouncer-advisory
kind: suppress
applies-to: reviews/outbox_handlers.py, reviews/reconciler.py
rule: Session-level advisory locks do not work with PgBouncer transaction pooling. This is documented and accepted (LOCK-4).
source: GLI, RVO

### M-migrations-worker-stopped
kind: suppress
applies-to: */migrations/*.py
rule: Data migrations may run with the worker stopped, when the spec says so. Do not flag a mixed-version race for a migration documented that way.
source: RVO 0008
