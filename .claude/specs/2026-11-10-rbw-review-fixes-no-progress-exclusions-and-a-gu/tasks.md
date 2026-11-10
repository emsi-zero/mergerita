# TASKS: RBW review fixes: No progress exclusions and a guarded worker loop

Spec: spec.md · Key: RWF · Route: task · State: done
Checkpoint: `.claude/rmo/bin/rmo gate-a`

## Session 1 — No progress exclusions and a guarded worker loop

State: done
Covers: RWF-1, RWF-2, RWF-3, RWF-4, RWF-5, RWF-6

- [x] T1 — `_no_progress` excludes a pending session today or later and an open assignment with a window now or later; the run's `now` is passed down, in `reviews/watchdog.py` — RWF-1, RWF-2, RWF-3
- [x] T2 — `run_guarded` around `reconcile_all` and the three daily jobs, with one alert per failing streak in its own transaction, in `reviews/management/commands/run_worker.py` — RWF-4, RWF-5, RWF-6
- [x] T3 — `restart: unless-stopped` on `web` and `worker` in `docker-compose.yml` (checked with `docker compose config`)
- [x] T4 — tests: RWF-1..3 in `reviews/tests/test_watchdog.py`, RWF-4..6 in `reviews/tests/test_worker_guard.py`; mutation-checked (no exclusions → RWF-1/2 fail; alert on every failure → RWF-5 fails; no guard → RWF-4..6 fail)
- [x] T5 — docs: README (No progress note, restart and job-failure note), docs/testing.md scenario Q step 6

Gate A: `.claude/rmo/bin/rmo gate-a --session 1 --log "Gate A"`
Gate B: rmo-reviewer (spec, style, query) · rmo-deep-reviewer (logic, security; families IO, TX)

Resume: done. Gate A green (128 passed, trace 6/6), Gate B green after round 1 fixes.
