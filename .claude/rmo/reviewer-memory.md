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

### M-daily-jobs-at-most-once
kind: suppress
applies-to: reviews/rebooking.py, reviews/watchdog.py
rule: The daily rebooking and the watchdog each run at most once per local day. A crash after the day claim loses that day's run or report, and a run that crosses midnight skips that day's watchdog report (the README says to set the run time well before midnight). Still a defect: two runs on one day, or one bad item that stops a run.
source: RBW design Failure modes, Session 3 review, 2026-11-08

### M-rebooking-more-time-same-day
kind: suppress
applies-to: reviews/rebooking.py
rule: The retry may rebook a reviewer who asked for more time on the same day as that session; it passes no `after_day` (RBW-4 does not require it). Still a defect: rebooking someone who handed the review back (RBW-5) or the author (RBW-6).
source: RBW Session 2 review, 2026-11-08

### M-rebooking-admin-summary-scoped
kind: suppress
applies-to: reviews/rebooking.py, reviews/admin.py
rule: After the "Retry booking" admin action, the summary lists only the selected MRs' leftovers, and a selected MR with nothing to book counts as skipped (RBW-11). Do not flag the mixed MR and area-review units in the skipped count.
source: RBW spec delta, 2026-11-08

### M-no-progress-any-future-slot
kind: suppress
applies-to: reviews/watchdog.py
rule: "No progress" leaves out an MR with any pending review session today or later, or any open merge window now or later (RWF-1, RWF-2), even when another area review of the same MR is stuck without a session. Still a defect: a past-only pending session that is not reported under "Missed slot" (RWF-3).
source: RWF review, 2026-11-10

### M-worker-guard-relies-on-restart
kind: suppress
applies-to: reviews/management/commands/run_worker.py, docker-compose.yml
rule: `process_next_event` and `dispatch_due` stay unguarded on purpose: an error there (for example a dead connection) ends the worker, and `restart: unless-stopped` starts a fresh process. Only the scheduled jobs are guarded. Still a defect: guarding those two calls without handling a dead connection, which would make the loop spin.
source: RWF review, 2026-11-10
