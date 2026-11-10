# DESIGN: Teams, reporting lines and the urgency workflow

Spec: spec.md · Key: URG

## Shape

- Teams and reporting lines are plain model fields with `clean()` validation,
  edited in the admin.
- The urgency workflow is one new service module, `reviews/urgency.py`. It holds
  the request, withdraw, decide and approve commands, `resolve_approver`, and
  the weekly team count. The panel views only parse input and call it.
- Urgency reaches booking and merge routing through **one derived flag**:
  - `book_area_review` and `assign_merge` read `merge_request.urgent_at is not None`
    when their new `urgent` argument is None (the default).
  - So every existing caller (`area_resolver`, `book_follow_up`, `hand_back`,
    `need_more_time`, `rebooking`, `coverage`) gets urgent treatment with no
    change (URG-19, URG-21).
  - The flag changes only the capacity check and the day choice (INV-4).

## Write paths and locks

| Command (module.function) | Locks, in order | GitLab read before? | Audit action | Outbox jobs |
|---|---|---|---|---|
| `urgency.request(author, mr_id, reason)` | MR row → re-read MR state and author (new statement) | none | `urgency.requested` | `notify` (approver, or admins) |
| `urgency.withdraw(author, request_id)` | MR row (by the request's MR id) → re-read request status | none | `urgency.withdrawn` | `notify` (approver, or admins) |
| `urgency.decline(person, request_id, note)` | MR row → re-read request status and rights | none | `urgency.declined` | `notify` (author) |
| `urgency.approve(person, request_id, note)` | MR row → re-read request status and rights → set `urgent_at` → `lock_booking()` → for each area review to move: AreaReview → its pending ReviewSession (supersede, release minutes) → CapacityDay rows (inside `book_area_review`); then the merge re-route when the MR is approved (Session 4) | none | `urgency.approved`, `area_review.booked`, `review_session.superseded`, (`merge.cancelled`, `merge.assigned`) | `notify` (author; approver and admins on URG-18; admins on URG-13), `set_reviewer` |
| `booking.book_area_review` (urgent) | the caller's | — | `area_review.booked` | `set_reviewer`, `notify`, and the URG-18 alert |
| `merge_router.assign_merge` (urgent) | the caller's (MR and `lock_booking()`) | — | `merge.assigned` | `notify` |

The request rows are written only under the MR lock (INV-1), so they need no
lock of their own. Every other writer of an area review either holds the MR
lock first (rebooking, area_resolver, coverage) or holds `lock_booking()`
(outcomes). The approval holds both, so URG-24 and URG-25 serialize.

A hand back that waits for `lock_booking()` while an approval runs then
re-reads the area review and its MR through `_lock_pending_session`, which
runs after `lock_booking()`. It sees the committed `urgent_at` and books with
the urgent rules (URG-25).

## The urgent booking (`booking.book_area_review`, `urgent=True`)

- **Candidates:** the same `_find_candidates`, `exclude` and `only_reviewer`,
  author barred, and `_lock_capacity` (working days and leave). INV-4 holds by
  construction.
- **`_pick_slot`:**
  - In urgent mode, the `free >= slot` check is skipped. Any candidate with a
    CapacityDay row (working and not on leave) fits.
  - The first such day from today wins, so an owner who works today is booked
    today.
  - The ranking within a day is unchanged (tier, then free minutes, which may
    be negative, then open bookings, then pk).
- **`_book`:**
  - `overage_minutes = max(0, min(slot, booked_after - budget))` and `is_urgent=True` on the session.
  - `booked_minutes` still grows, so that day takes no normal booking.
  - Other days' rows are untouched (URG-20).
- **URG-18:** when the booked day is after today, or nothing fits, queue the
  alert to the approver of the MR's approved request plus every admin.
- **Approval path, per area review to move:**
  1. Supersede a pending session on a later day and release its minutes (the `book_follow_up` pattern).
  2. Book with `rebooking.book_with_continuity()`, the renamed public `rebooking._book`: previous reviewer first, nobody who handed it back.

## Approver resolution (`urgency.resolve_approver(author, today)`)

1. Set `candidate = author.reports_to` and `visited = {author.pk}`.
2. While `candidate` is not None and not in `visited`:
   - Add `candidate` to `visited`.
   - If `candidate` is available today (not on leave) and is not the author, return it.
   - Otherwise look for a delegate: first the author's team, if `candidate` leads it, then the lowest-pk team `candidate` leads.
   - If that delegate is available and is not the author, return the delegate.
   - Otherwise set `candidate = candidate.reports_to` and go on.
3. Return None. The request then goes to the admins, and the visited set stops a cycle (INV-5).

## Decisions

| Decision | Owner | Invariant it protects | Alternative rejected |
|---|---|---|---|
| `urgent` derived from `MergeRequest.urgent_at` inside `book_area_review` and `assign_merge` | booking, merge_router | INV-4, URG-19, URG-21 | Passing a flag through 6 callers: easy to miss one |
| The approval reuses the rebooking's continuity booking | rebooking | URG-15 | A third copy of "previous reviewer first" |
| The approver is stored on the request at creation | urgency | URG-8, URG-10 | Re-resolving at decision time: the person shown in the tab would change |
| Admins may decide any pending request | urgency | URG-9 | Only the named approver: a request can get stuck |
| My reviews sorting: `(day, not is_urgent, pk)` | panel | URG-22 | A separate urgent list |
| The weekly count is approved requests of members of the requester's team, last 7 local days, by `decided_at` | urgency | URG-13 | A calendar week |

## Data and migrations

One migration, `reviews/0014_teams_and_urgency.py`, with these changes:

- `Team` (`name` unique, `lead` FK Person with `on_delete=PROTECT`, `delegate` FK Person nullable with `SET_NULL`, `weekly_urgent_limit` nullable).
- `Person.team` (FK Team, nullable) and `Person.reports_to` (FK self, nullable), both with `on_delete=SET_NULL`.

  PROTECT and SET_NULL act in Django (the FKs keep `db_constraint=False`). The
  admin refuses to delete a person who leads a team, and deleting a team, a
  delegate or a manager clears the references instead of leaving ids to
  missing rows, which would make the panel header raise (Session 1 review M1).
- `UrgencyRequest`, with partial unique constraint `urgency_one_active` on
  (merge_request) where status in (pending, approved), and an index on
  (approver, status) for the tab.
- `MergeRequest.urgent_at` (nullable).
- `ReviewSession.is_urgent` (`db_default=False`) and `overage_minutes` (`db_default=0`).
- `MergeAssignment.over_limit` (`db_default=False`).

There is no data migration, and the worker may keep running: every new
column is nullable or has a `db_default`. The FKs follow the repository style
(`DO_NOTHING`, `db_constraint=False`).

## Failure modes

| Input or fault | Behaviour | Criterion or INV |
|---|---|---|
| A double click on approve, or two approvers | The MR lock serializes them. The second one re-reads the status and gets "no longer pending" | URG-12 |
| Approve vs withdraw | The same: whichever takes the MR lock first wins | URG-12 |
| The approval raises while booking (for example the INV-4 `ValueError`) | The whole transaction rolls back. The request stays pending and the panel shows an error | INV-2 |
| No owner today | Earliest available day, plus an alert | URG-18 |
| A cycle in reporting lines from concurrent admin edits | The visited set ends the walk. The request goes to the admins | INV-5 |
| The approver's Person has no panel user | Admins can still decide | URG-9 |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The derived flag changes existing booking tests | Medium | Red suite | Default to None → derive; no existing MR has `urgent_at`, so behaviour is unchanged |
| The approval transaction is long (several area reviews under `lock_booking()`) | Low | Webhooks wait | MRs have a handful of area reviews; one short transaction |
| The urgent ranking with negative free minutes | Low | Odd choice of owner | Ranking documented; the URG-15 test pins continuity and tier |
