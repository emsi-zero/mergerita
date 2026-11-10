# SPEC: RBW review fixes: No progress exclusions and a guarded worker loop

Key: RWF · Route: task · Risk: high
Created: 2026-11-10

Risk is high: the change alters how the worker loop treats a failing batch job
(IO-2), next to inbox claiming and outbox dispatch.

## Problem

Two findings from the review of the RBW spec. First, the watchdog's "No
progress" case counts working days since `state_changed_at` only. It reports
an `in_review` MR whose next review session is booked on a future day, and an
`approved` MR whose merge window is in the future. Both are waiting for a
booked slot, not stuck, so admins get false alerts. Second, `run_worker` calls
`reconcile_all` and the three daily jobs with no guard, and docker-compose has
no restart policy. One exception in a scheduled job ends the worker process,
and inbox and outbox work stop until someone restarts it by hand.

## Non-goals

- No change to the other watchdog cases, the thresholds or the alert format.
- No retry of a failed daily job on the same day. Its day stays claimed (INV-1
  of RBW).
- No change to `process_next_event` or `dispatch_due`. They already quarantine
  or back off per item.
- No health check, process supervisor or metrics beyond the compose restart policy.
- No restart policy for `db`, `migrate`, `gitlab-mock` or `tests`.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| RWF-1 | WHILE an MR has a pending review session whose day is today or later, THE watchdog SHALL NOT report it under "No progress". | REGRESSION | An MR waiting for its booked review day is reported as stuck |
| RWF-2 | WHILE an MR has an open merge assignment whose window is now or later, THE watchdog SHALL NOT report it under "No progress". | REGRESSION | An approved MR waiting for its merge window is reported as stuck |
| RWF-3 | WHILE an MR's only pending session has a day before today and its state has not changed for more than the stale threshold, THE watchdog SHALL report it under "Missed slot" and SHALL NOT report it under "No progress". | REGRESSION | Excluding pending sessions hides a missed slot, or reports it twice |
| RWF-4 | IF a scheduled job (`reconcile_all`, `run_daily_retry`, `run_daily_rebooking` or `run_daily_watchdog`) raises, THEN THE worker loop SHALL log the exception, SHALL queue an admin alert in its own transaction through `alerts.queue_admin_alert`, and SHALL go on to `process_next_event` and `dispatch_due` in the same pass. | IMPORTANT FAILURE MODE | One failing job stops the worker, and the inbox and outbox stall |
| RWF-5 | WHILE the same scheduled job keeps raising on later passes, THE worker loop SHALL queue no further alert for it until that job has completed once without raising. | IMPORTANT FAILURE MODE | A failing reconcile sends an admin alert every 5 minutes |
| RWF-6 | IF a daily job raises after it claimed the day, THEN THE worker loop SHALL NOT run that job again on the same local day. | REGRESSION | The guard turns a failing job into a retry storm |

Not tested, and why:
- `restart: unless-stopped` on the `web` and `worker` services in
  `docker-compose.yml` — configuration. It is checked with
  `docker compose config` when the stack is restarted.
- That queuing the alert itself raises (for example, the database is down)
  is caught and logged, and the loop goes on — FRAMEWORK BEHAVIOR of the same
  `except` path. RWF-4's test covers the path.

## Invariants

- Holds: TX-1, TX-3, IO-2, DATA-2, and RBW INV-1 (each daily job runs at most
  once per local day) and RBW INV-4 (the watchdog writes nothing but its
  claim, audit event and outbox rows).
- INV-1 — a scheduled job's exception never leaves `handle()`. The loop
  always reaches `process_next_event` and `dispatch_due` — owned by
  `run_worker` — enforced by one guard function around each scheduled call.
- INV-2 — the alert is queued in its own `transaction.atomic()`, outside any
  transaction of the job that failed — owned by `run_worker` — enforced by the
  guard.

## Contracts

- `docker-compose.yml`: `restart: unless-stopped` on `web` and `worker`.
- New admin alert text: `Worker job <name> failed: <ExceptionType>: <message>. It is skipped until its next run; see the worker log.`
- Behaviour change: the "No progress" exclusions are those of RBW-16 plus
  RWF-1 and RWF-2.
- No migration, no setting, no audit action.

## Validation decisions

- None. No input boundary changes.

## Docs

- README, "Rebooking and the watchdog": one line saying that "No progress"
  skips MRs waiting for a future session or merge window.
- README, "Run it with Docker Compose": one line saying that the web and the
  worker restart automatically, and that a failing scheduled job sends one
  admin alert.
- No new `docs/testing.md` scenario. Scenario Q gains one step: an MR booked
  on a future day is not reported.

## Checkpoint

`.claude/rmo/bin/rmo gate-a` (tests `scripts/test.sh -q`, pylint, isort, black, makemigrations --check, trace)

## Open questions

| Question | Options | Decision |
|---|---|---|
| Alert on every failure, or once per failing streak? | (a) every failure, as written in the request; (b) the first failure, then quiet until the job succeeds once | (b): the daily jobs fail at most once a day either way, but `reconcile_all` runs every 300 s, so (a) would send about 288 alerts a day while GitLab is down (RWF-5). The streak is kept in the worker process's memory, so a restart alerts again once |
| What counts as a future window | `window >= now`; `window` null | `window >= timezone.now()`. An open assignment with a null window is not excluded: nobody is waiting for a slot |
| A pending session with `day == today` | counts as future or past | future (`day >= today`), as written in the request. A session booked today is still in progress |
