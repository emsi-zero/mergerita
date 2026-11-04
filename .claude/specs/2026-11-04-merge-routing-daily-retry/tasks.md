# TASKS: Merge routing daily retry

Spec: spec.md · Key: MRDR · Route: task · State: done
Checkpoint: `scripts/test.sh -q`

## Session 1 — Merge routing daily retry

State: done · Covers: MRDR-1, MRDR-2, MRDR-3, MRDR-4, MRDR-5, MRDR-6

- [x] T1 — `reviews/models.py`: `MergeRoutingSettings.daily_retry_time`, `last_retry_on`; `MergeRequest.approved_at`; migration 0012 — MRDR-1, MRDR-3, MRDR-6
- [x] T2 — `reviews/coverage.py`: set `approved_at` when the MR becomes `approved` — MRDR-3
- [x] T3 — `reviews/merge_router.py`: `assign_merge(..., alert_on_no_fit=True)`; `reviews/merge_retry.py`: `retry_unassigned(merge_request_ids=None, trigger)` and `run_daily_retry(now)` with the day claim — MRDR-1, MRDR-2, MRDR-3, MRDR-5, INV-1..3
- [x] T4 — `reviews/management/commands/run_worker.py`: call `run_daily_retry` on every pass — MRDR-1
- [x] T5 — `reviews/admin.py`: merge request admin with the "Retry merge routing" action; settings admin shows `daily_retry_time` — MRDR-4, MRDR-6
- [x] T6 — Tests in `reviews/tests/test_merge_retry.py` (MRDR-1..6); docs (`docs/testing.md` scenario, README admin note)

Gate A: `bin/craft-spec trace` green · checkpoint green through `bin/craft-check` · pylint, isort, black green · makemigrations --check clean
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1..3, the day claim, the lock order)

Resume: Done. Committed.
