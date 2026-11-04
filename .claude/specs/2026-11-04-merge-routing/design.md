# DESIGN: Merge routing

Spec: spec.md · Key: MRG

## Shape

- **`reviews/merge_router.py`** (domain service):
  - `assign_merge(merge_request)` runs inside the caller's transaction. The caller already holds the MR row lock and the global booking lock: it is called from `coverage` at the moment the MR becomes `approved`, and coverage takes MR → `lock_booking()` → rows (INV-6 of the review spec). Counting and inserting under that lock gives INV-1.
  - `cancel_open_assignment(merge_request, reason)` serves MRG-11.
- **`reviews/merges.py`** (domain service): `record_merge(merge_request, merged_by_gitlab_id, merged_at)` is the single path for webhook and reconciliation merges (INV-3, INV-5). It classifies the merge: violation, before approval, outside window, by another person.
- **Lifecycle**: the Merge Request Hook with action `merge`, and the reconcile `merged` event, only queue the outbox job `confirm_merge` for the MR (when it is not yet `merged`). The hook payload is a trigger, not a source of truth.
- **`confirm_merge` (outbox job, in `reviews/merges.py`)**: with no transaction open, it reads `get_merge_request`; if GitLab does not show the MR merged, it stops. Then it reads the verdict state of each approved area review's reviewer (the coverage freshness rule). Then, in one atomic block under the MR lock, it calls `record_merge` with the merger, `merged_at` and the set of reviewers whose approval is still fresh.
- **Reconciler**: after the open-MR pass, it reads each MR the orchestrator holds as `approved`, through the gateway's `get_merge_request`, with no transaction open. If GitLab reports one as merged, it stores an inbox event (source `reconcile`, action `merged`, UUID `project:iid:merged:<merged_at>`). The lifecycle handles that event with the same `record_merge`.
- **Coverage** (MRG-11): coverage also runs while the MR is `approved`, so the lifecycle queues it for MRs in that state too. If an area review loses its fresh approval, coverage resets the approval and books the follow-up as RVO-7 already does, moves the MR to `in_review`, and calls `cancel_open_assignment`, which chats the merge owner.
- **Panel**: `MyMergesView` at `/panel/merges/`, behind `PanelAccessMixin` and gated on `person.is_merge_owner`. Anyone else gets 404. The tab shows only for merge owners.

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| **Merge window datetimes.** For each day from today up to the horizon that is one of the person's working days and not covered by leave, each `HH:MM` in `merge_times` becomes an aware datetime in `TIME_ZONE` (Asia/Tehran). Windows earlier than now are skipped | `merge_router` | MRG-1 | Storing windows as rows (the developer edits times as a list on the person) |
| **Ranking.** Each candidate gets their earliest feasible window: a day where their count of non-cancelled assignments is below their limit. Candidates sort by the day of that window, then the count that day, then the window time, then person id | `merge_router` | MRG-1 | A score (harder to explain in the reason) |
| **Candidates.** People with `is_merge_owner=True`, excluding the author and every current reviewer of the MR's area reviews, all loaded in one query. The exclusion is checked again before save (INV-2) | `merge_router` | INV-2 | — |
| **Effective limit.** `person.daily_merge_limit` if set, else `MergeRoutingSettings.load().default_daily_merge_limit`. The singleton is `pk=1`, created by a data migration, and `load()` does a `get_or_create` | `reviews.models` | MRG-3 | Settings constants (the developer wants the limit editable in the admin) |
| **Merge time validation.** Field validators cover the limits and the window length (`PositiveIntegerField` for the daily limit, `MinValueValidator(1)` for the window length). A validator on `Person.merge_times` rejects any entry that is not `HH:MM` 00:00–23:59, so `full_clean()` enforces it on any write path. There is no `MergeRoutingSettings.clean()`. The admin uses model forms, so the validators run there. The person form edits merge times as a comma-separated text field mapped to the list | `reviews.models`, `reviews.admin` | MRG-10 | Validating only in the UI |
| **Approvers.** The current reviewers of the MR's area reviews in state `approved`. Any of them who merges is an `approver` violation. If GitLab no longer shows a fresh approval from one of them, the merge counts as `before_approval` (MRG-7) | `merges` | MRG-6, MRG-7 | GitLab's approvals API (the spec defines approvers this way) |
| **Merge classification.** `violation`: the merger is the author → `author`; else the merger is an approver → `approver`; else the MR was not `approved` → `before_approval`; else `none`. `outside_window`: the merge was before the window or after window + `merge_window_minutes`. `by_other_person`: a merge owner was assigned and the merger is not that owner. An admin alert goes out when `violation != none`, or when the merger is unknown (MRG-12) | `merges` | MRG-6..8 | — |
| **Merging an MR with no assignment** (merged before approval, or no fit). A `MergeAssignment` row is created at merge time with `merge_owner=None`, so every merge is recorded in one table | `merges` | INV-3 | — |
| **Gateway `get_merge_request`.** Returns `state`, merger id (`merge_user.id`, else `merged_by.id`, else none), `merged_at` from `GET /projects/:id/merge_requests/:iid`, under the same deadline and error rules as the other calls. A malformed body raises `GitLabError`. The fake and the mock implement it; the mock sets `merge_user` | `integrations` | MRG-5, MRG-9, MRG-12 | Trusting the hook's `user` |
| **Reconciler per-MR guard.** Any error reading one approved MR is logged and skipped, so the other MRs' merges are still stored | `reviews.reconciler` | MRG-9 | — |
| **Time zone.** `TIME_ZONE = "Asia/Tehran"`. Every "today" already uses `timezone.localdate()` | `orchestrator.settings` | — | — |

## Data and migrations

One `reviews` migration in S1 (0010), and 0011 in S2 (partial unique: one open assignment per MR):
- `AddField` ×3 on Person, all with defaults or nullable.
- `CreateModel` MergeRoutingSettings and MergeAssignment.
- `RunPython` to create the settings row.

Rollback: reverse the migration.

## Sessions

- **S1:** settings, models, admin, the router and its hook into coverage approval, plus My merges (MRG-1, 2, 3, 4, 10).
- **S2:** merge detection (webhook, reconciliation, `confirm_merge`, classification), revocation while approved, the mock `merge` control, the gateway method, the e2e test and docs (MRG-5, 6, 7, 8, 9, 11, 12).

## Standards review

| Standard | Verdict |
|---|---|
| django: Configuration and business constants | satisfied — the limits live in the DB, which is the tier-config exception; the window length lives in the DB too |
| django: Transactions | satisfied — the router runs in coverage's transaction, and the reconciler reads GitLab outside any transaction |
| django: FK DO_NOTHING + db_constraint=False | satisfied |
| Concurrency | satisfied — assignment happens under the MR lock plus the global booking lock (INV-1); a merge is recorded under the MR lock (INV-5) |
| TS-5 | satisfied — My merges uses select_related/prefetch |
| implementation: Tests | satisfied — one test per MRG criterion; the e2e has an approver who merges in the mock, which gives a violation alert |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Changing TIME_ZONE moves "today" for existing tests and data | Medium | Low | The full suite runs after the switch; tests use `localdate()` |
| GitLab hook `merged_at` field naming | Low | Low | Fall back to `updated_at`, then `received_at`; on the smoke list |
