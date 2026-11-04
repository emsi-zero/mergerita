# TASKS: Merge routing

Spec: spec.md · Key: MRG · Route: project · State: done
Checkpoint: `scripts/test.sh -q`

## Session 1 — Merge owners, settings, router, My merges

State: done · Covers: MRG-1, MRG-2, MRG-3, MRG-4, MRG-10

- [x] T1 — Settings: `TIME_ZONE = "Asia/Tehran"`. Models:
  - `Person.is_merge_owner`, `merge_times` and `daily_merge_limit`, with `clean()`.
  - `MergeRoutingSettings` singleton with `load()` and `clean()`.
  - `MergeAssignment`.
  - `MergeRequestState.MERGED`.

  One migration, with the settings-row data step — MRG-3, MRG-10
- [x] T2 — Admin:
  - The Person form gets the merge-owner fields, with merge times as comma-separated text.
  - A `MergeRoutingSettings` admin that edits only the single row (no add or delete).
  - `MergeAssignment` read-only.

  — MRG-10
- [x] T3 — `reviews/merge_router.py` `assign_merge`: windows, candidates, ranking, the limit, the reason, audit and chat notify, plus the no-fit admin alert. Call it from coverage where the MR becomes `approved` — MRG-1, MRG-2, MRG-3, INV-1, INV-2
- [x] T4 — Panel:
  - `MyMergesView` at `/panel/merges/` and the template.
  - A tab in `base.html`, shown only for merge owners.
  - 404 for everyone else.
  - Use the My reviews look (`btn`, cards).

  — MRG-4
- [x] T5 — Tests:
  - `reviews/tests/test_merge_router.py`: MRG-1, MRG-2, and MRG-3 with a two-thread concurrency case.
  - `reviews/tests/test_merge_settings.py`: MRG-10, through `full_clean()` and the admin form.
  - `panel/tests/test_merges.py`: MRG-4.
  - Add `/panel/merges/` to the access tests.
  - Confirm the full suite is green after the `TIME_ZONE` change.

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green · makemigrations --check clean
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1, INV-2, the window maths and time zone)

Resume: Done: models, migration 0010, admin, reviews/merge_router.py (called from coverage._approve), My merges at /panel/merges/, tests. TIME_ZONE is Asia/Tehran. The existing RVO-7 test now expects merge.unassigned (no merge owners). Next: Session 2, merge detection; STATE_BADGES, STATE_SUMMARIES and NEXT_STEPS in panel/views.py need a `merged` entry.

## Session 2 — Merge detection, violations, revocation

State: done · Covers: MRG-5, MRG-6, MRG-7, MRG-8, MRG-9, MRG-11, MRG-12

- [x] T1 — `reviews/merges.py` `record_merge`. Classify the merge (violation, before approval, outside window, by another person), write audit, alert admins on a violation, and move the MR to `merged`. It is idempotent under the MR lock — MRG-5, MRG-6, MRG-7, MRG-8, INV-3, INV-5
- [x] T2 — Lifecycle: a Merge Request Hook with action `merge` calls `record_merge`. Coverage is also queued for MRs in `approved` — MRG-5, MRG-11
- [x] T3 — Gateway: `get_merge_request` in the interface, real gateway, fake and mock. Mock: a `merge` control and CLI command that set state, merged by and merged at, and send the hook. Reconciler: read approved MRs outside the transaction, and store a `merged` inbox event — MRG-9
- [x] T4 — Coverage while `approved` (MRG-11): a lost approval sends the MR back to `in_review` and RVO-7's follow-up applies; `cancel_open_assignment` runs and the merge owner is chatted — MRG-11
- [x] T5 — Tests:
  - `reviews/tests/test_merges.py`: MRG-5 to MRG-8.
  - `reviews/tests/test_reconciler.py` or a new file: MRG-9.
  - `reviews/tests/test_coverage.py` or a new file: MRG-11.
  - e2e: an approver merges in the mock, giving a violation alert and the MR `merged`.
- [x] T7 — Review round 1 fixes (spec delta 2026-11-04):
  - `confirm_merge` outbox job: hook and reconcile only queue it; it reads `get_merge_request` and the reviewers' verdicts outside the transaction, then calls `record_merge` (MRG-5, MRG-7, INV-3, INV-4).
  - Gateway, fake and mock: `merge_user` first, then `merged_by`; a malformed body raises `GitLabError`.
  - Unknown merger: record and alert admins (MRG-12).
  - `by_other_person` false when no owner; panel shows "no merge owner assigned" (MRG-8), with a panel test for the MRG-8 labels.
  - Reconciler: catch any error per MR (MRG-9), test with one bad and one good MR.
  - MRG-11 test: re-approve after the revoke and assert a new open assignment beside the cancelled one.
  - Tests for MRG-5 (unconfirmed hook changes nothing), MRG-7 (revoke then merge), MRG-12.
  - Docstrings: `_violation`, `_alert`, `_history_label`; the e2e test docstring names its criterion.
- [x] T6 — Docs:
  - `docs/testing.md`: a merge routing scenario, plus smoke items for the merge hook fields and `merged_by`.
  - README: the merge owner setup in the admin.

Gate A: `bin/craft-spec trace --session 2` green · `bin/craft-spec trace` green · checkpoint green · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-3, INV-4, INV-5, the classification, and revocation)

Resume: Done: reviews/merges.py record_merge, lifecycle `merge`/`merged` actions, coverage runs on approved MRs (revoke -> in_review, merge.cancelled), gateway get_merge_request, mock `merge` control, reconciler _reconcile_merges, migration 0011 (one open assignment per MR), panel merged entries, tests, e2e, docs. Next: Gate B reviews, then `/finish`.
