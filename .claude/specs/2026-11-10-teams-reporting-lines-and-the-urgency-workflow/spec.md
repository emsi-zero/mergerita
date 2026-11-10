# SPEC: Teams, reporting lines and the urgency workflow

Key: URG · Route: project · Risk: high
Created: 2026-11-10

Risk is high: a new approval path in the panel (a new permission boundary,
AUTH), booking over the daily budget under `lock_booking()` (LOCK), an approval
that books in the same transaction as it decides (TX), races with the daily
rebooking and a hand back, and migrations.

## Problem

The proposal lets a developer mark an MR as urgent. The person they report to
approves it, and the review is then booked today with a qualified code owner,
even when that owner's daily budget is used up. None of this exists. `Person`
has no team or manager, booking always respects `person_budget()`, and an
urgent MR waits like any other. Teams and reporting lines are the
prerequisite: they decide who may approve.

## Non-goals

- No directory or HR sync. Teams and reporting lines are entered in the admin.
- No approval by chat button. The chat adapter is still a fake.
- No escalation of stuck MRs to leads, no organisation minimum budget, no
  continuation reserve.
- No undo of an approved urgency. Withdrawal applies to a pending request only.
- No reassignment of a pending request when its approver goes on leave after
  it was made. Admins can decide any request (URG-9).
- Urgency never relaxes availability: leave, working days, the author rule,
  ownership. It relaxes only the daily review budget and the daily merge limit.
- No change to the AI gate, coverage or freshness rules (GL-2).
- No new GitLab call or write (GL-3).

## Acceptance criteria

### Part 1 — teams and reporting lines

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| URG-1 | THE admin SHALL let an admin create a Team (name, lead, optional delegate, optional weekly urgent limit) and set each Person's team and `reports_to`. | BUSINESS RULE | Nobody can be configured as an approver |
| URG-2 | IF an admin sets a `reports_to` that is the person themselves, or that would make the reporting line loop back to them, THEN THE admin SHALL reject it with an error that names the loop. | REGRESSION | Approver resolution never ends, or a person approves their own request through a loop |
| URG-3 | IF an admin sets a team's delegate to the team's lead, THEN THE admin SHALL reject it. | BUSINESS RULE | A lead on leave "delegates" to themselves and nobody can approve |
| URG-4 | THE panel header SHALL show the signed-in person's team and the person they report to, when set. | BUSINESS RULE | A developer cannot see who will approve their urgency |

### Part 2 — request, approver, decision

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| URG-5 | WHEN the author of an MR in `awaiting_ai_review`, `awaiting_booking`, `in_review` or `approved` submits an urgency request with a reason of 1 to `RMO_URGENCY_REASON_MAX_LENGTH` characters on My MRs, THE panel SHALL store one pending request, notify its approver (or every admin, URG-8), and write `urgency.requested`. A request for an MR in another state, by a non-author, or with an empty or over-long reason SHALL be refused with a message. | BUSINESS RULE | A merged MR or someone else's MR is marked urgent |
| URG-6 | WHILE an MR has a pending or approved urgency request, THE panel SHALL refuse a second request for it. Two requests submitted at once SHALL leave exactly one (a partial unique constraint). | CONCURRENCY | Two pending requests for one MR, or an MR approved twice |
| URG-7 | WHEN the author withdraws a pending request, THE panel SHALL mark it withdrawn, notify the approver, and write `urgency.withdrawn`. A decided request cannot be withdrawn. | BUSINESS RULE | An approver acts on a request that no longer exists |
| URG-8 | WHEN a request is made, THE request SHALL get its approver from the author's `reports_to`. When that person is on leave today, it goes to the delegate of a team they lead (the author's team first). When there is no delegate, or the delegate is also on leave, it goes one level up to their own `reports_to`, with the same rules. Nobody in this chain is ever the author. When the chain ends with no approver, the request goes to the admins (approver empty). | BUSINESS RULE | A request waits on someone on leave, or nobody can decide a lead's request |
| URG-9 | WHEN the request's approver, or any admin, opens the "Urgency requests" tab, THE panel SHALL list the pending requests they may decide, with the MR, author, reason, and the team's approved count this week against its limit. They SHALL be able to approve or decline with an optional note. The tab SHALL appear only for a person who may decide at least one request, or who is an admin. | BUSINESS RULE | Approvers cannot find or act on requests |
| URG-10 | IF the deciding person is the request's author, or is neither its approver nor an admin, or the request is no longer pending, THEN THE panel SHALL refuse the decision with 404 and change nothing. | SECURITY BOUNDARY | An author approves their own request, or someone else's request is decided |
| URG-11 | WHEN a request is declined, THE panel SHALL mark it declined with the note, notify the author, and write `urgency.declined`. The MR's bookings SHALL NOT change. | BUSINESS RULE | A decline still changes bookings |
| URG-12 | WHEN two people decide the same request at the same time, or the author withdraws it while it is being decided, THE system SHALL apply exactly one decision. The other SHALL get a "no longer pending" message. | CONCURRENCY | A request ends both approved and declined, or is approved after it was withdrawn |
| URG-13 | WHILE the requester's team has reached its weekly urgent limit (approved requests in the last 7 local days), THE approval SHALL still be allowed. The tab SHALL show the warning before the decision, and the approval SHALL alert the admins once with the count. | BUSINESS RULE | Urgency is abused with nobody noticing, or a real emergency is blocked |

### Part 3 — effect on booking

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| URG-14 | WHEN a request is approved, THE approval SHALL, in the same transaction: mark the MR urgent, write `urgency.approved`, notify the author, and book today every area review of the MR that is unbooked or booked for a later day. Approved area reviews and ones booked today SHALL stay as they are. | CRITICAL BEHAVIOR | An approved MR stays booked on a later day, or an approved area's approval is lost |
| URG-15 | WHILE booking urgently, THE booking SHALL ignore the daily review budget, but SHALL only book an owner who works today, is not on leave, and is not the author. It SHALL try the area review's current or most recent non-handed-back reviewer first, then other owners in the normal tier and load order. | REGRESSION | An urgent review lands on someone on leave or on a day off, on the author, or loses continuity |
| URG-16 | WHEN an urgent booking puts a reviewer's day over budget, THE booking SHALL record the minutes over budget on the new session (`overage_minutes`) and mark it urgent. The reviewer's CapacityDay SHALL count the slot as booked. | BUSINESS RULE | Overage cannot be reported per team, or the over-budget day accepts more normal bookings |
| URG-17 | WHEN an area review was booked for a later day, THE approval SHALL close that session as superseded and release its minutes before booking today. | REGRESSION | The reviewer keeps a ghost booking and budget on the later day |
| URG-18 | IF no owner works today and is available, THEN THE urgent booking SHALL book the earliest day in the horizon with an available owner (still ignoring budget), and SHALL alert the approver and the admins. IF none fits in the horizon, the area review becomes unbooked, and the same people are alerted. | IMPORTANT FAILURE MODE | An urgent MR silently waits, or is booked on someone's day off |
| URG-19 | WHEN an MR approved as urgent gets its area reviews later (it was in the AI gate), or gets a follow-up, hand back or need-more-time booking before it is merged, THE booking SHALL use the urgent rules of URG-15 and URG-16. | BUSINESS RULE | Urgency is lost after a push or a hand back |
| URG-20 | WHILE booking for an urgent MR, THE booking SHALL never make a normal booking fail for another MR on another day: overage is only on the urgent session's day. | REGRESSION | Overage today blocks a normal booking tomorrow |
| URG-21 | WHEN the daily rebooking runs, THE rebooking SHALL take the unbooked area reviews of urgent MRs first, with the urgent rules, then the others by oldest wait. | BUSINESS RULE | An urgent unbooked review waits behind old non-urgent ones |
| URG-22 | WHEN a reviewer opens My reviews, THE panel SHALL list a day's urgent sessions first and mark them "Urgent". | BUSINESS RULE | A reviewer does the urgent review last |

### Part 4 — merge routing

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| URG-23 | WHEN an urgent MR is approved for merge (or is already approved when the urgency is approved), THE merge router SHALL give it the earliest merge window, from now within the horizon, ignoring the merge owner's daily merge limit but not the other rules (not the author or a reviewer, working, not on leave). It SHALL record `over_limit` on the assignment when the limit was exceeded. An open assignment on a later day SHALL be cancelled first, with the existing cancel message. | BUSINESS RULE | An urgent approved MR waits days for a merge window |

### Concurrency

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| URG-24 | WHEN an approval and the daily rebooking handle the same MR at the same time, THE system SHALL leave each area review with exactly one pending session, booked today. | CONCURRENCY | A double booking, or the rebooking moves the urgent review to a later day |
| URG-25 | WHEN an approval and a hand back of the same MR's session happen at the same time, THE system SHALL leave the area review with one pending session today, never with the reviewer who handed it back. | CONCURRENCY | A hand back is undone, or two pending sessions exist |

Not tested, and why:
- The admin list and edit pages for Team — FRAMEWORK BEHAVIOR. URG-1's test covers that saving works and the validators are in place.
- The chat texts' exact wording beyond the contracted ones — covered by the
  URG-5, 11, 13 and 18 tests through the outbox rows.

## Invariants

- Holds: LOCK-1, LOCK-2, LOCK-3, LOCK-5, LOCK-6, TX-1, TX-2, TX-3, IO-2, GL-3,
  SEP-1, SEP-2, AUTH-1, AUTH-2, AUTH-4, AUTH-5, DATA-1, DATA-2, DATA-3, DATA-5.
- INV-1 — every write to an urgency request takes the MR row lock first, then
  re-reads the request's status in a new statement — owned by `reviews.urgency`
  — enforced by the request command shape.
- INV-2 — an approval decides and books in one transaction: MR lock →
  `lock_booking()` → AreaReview → ReviewSession → CapacityDay — owned by
  `reviews.urgency.approve` — enforced by one `transaction.atomic()`.
- INV-3 — at most one pending or approved request per MR — owned by the model —
  enforced by a partial unique constraint.
- INV-4 — urgency relaxes only the review budget and the daily merge limit; the
  candidate filters (ownership, author, leave, working days, hand-back
  exclusion) are the same code path as a normal booking — owned by
  `reviews.booking` — enforced by one `urgent` flag that changes only the
  capacity check and the day range.
- INV-5 — approver resolution terminates and never returns the author, even with
  a cycle introduced by concurrent admin edits — owned by `reviews.urgency` —
  enforced by a visited set.

## Contracts

- Migrations `reviews` 0014:
  - the `Team` model (name, lead, delegate, `weekly_urgent_limit`, nullable);
  - `Person.team` and `Person.reports_to`, both nullable;
  - the `UrgencyRequest` model (merge request, requester, approver nullable,
    reason, status pending/approved/declined/withdrawn, note, decided_by,
    created_at, decided_at), with partial unique constraint
    `urgency_one_active` on (merge_request) where status in (pending, approved);
  - `MergeRequest.urgent_at`, nullable;
  - `ReviewSession.is_urgent` and `ReviewSession.overage_minutes`, both with `db_default`;
  - `MergeAssignment.over_limit`, with `db_default=False`.
  - No data migration.
- New setting `RMO_URGENCY_REASON_MAX_LENGTH` (default 500).
- `booking.book_area_review(..., urgent: bool = False)`. Existing callers do not change.
- `merge_router.assign_merge(..., urgent: bool = False)`.
- Panel URLs:
  - `mrs/<pk>/urgency/` (POST, request)
  - `mrs/<pk>/urgency/withdraw/` (POST)
  - `urgency/` (the tab)
  - `urgency/<pk>/approve/` (POST)
  - `urgency/<pk>/decline/` (POST)
- Admin: Team (list and edit), and the Person form gets `team` and `reports_to`.
- Audit actions: `urgency.requested`, `urgency.withdrawn`, `urgency.approved`,
  `urgency.declined`. Existing ones are reused: `area_review.booked`,
  `review_session.superseded`, `merge.assigned`, `merge.cancelled`.
- Chat and alert texts:
  - to the approver: `Urgency requested for MR <p>:<iid> by <author>: <reason>. Decide in the panel.`
  - to the author: `Your urgency request for MR <p>:<iid> was approved.` / `... was declined: <note>`
  - to the approver: `The urgency request for MR <p>:<iid> was withdrawn.`
  - to the approver and admins: `Urgent MR <p>:<iid>: no owner is available today; booked on <day>.` / `...; no owner fits within <n> days.`
  - to the admins: `Team <name> has <n> approved urgent requests in 7 days (limit <m>).`

## Validation decisions

- `reports_to` is not self and has no cycle — REQUIRED (INV-5, URG-2) — `Person.clean()`, shown in the admin form.
- The delegate is not the lead — REQUIRED (URG-3) — `Team.clean()`.
- The reason is 1–max characters — REQUIRED — the panel form.
- The MR state and authorship are checked again under the MR lock — REQUIRED (LOCK-2) — `reviews.urgency`.
- The decision rights are checked again under the MR lock — REQUIRED (URG-10, URG-12) — `reviews.urgency`.

## Docs

- `docs/testing.md` §4, scenario **R** "Urgency", run through the mock: set up a
  team and reporting lines, open an MR booked on a later day, request
  urgency, approve as the lead, see today's booking with overage, then a
  decline and a withdraw. Marked "(unverified)".
- README: a new subsection "Teams and urgency" (admin setup: teams, leads,
  delegates, `reports_to`, the weekly limit; what urgency does and does not
  override).
- `.claude/rmo/INVARIANTS.md`: add SEP-3 ("an urgency request is never
  approved by its author") and AUTH-7 ("urgency decisions are scoped to the
  request's approver or an admin"). The next scenario letter becomes S.

## Checkpoint

`.claude/rmo/bin/rmo gate-a` (tests `scripts/test.sh -q`, pylint, isort, black, makemigrations --check, trace)

## Open questions

| Question | Options | Decision |
|---|---|---|
| Approver source | (a) `Person.reports_to`; (b) `team.lead` | (a) `reports_to` is the source of truth, as the proposal says "the person the requester reports to". A lead's approver is simply the person they report to. Team is used for grouping, for the delegate, and for the weekly limit |
| Top of the chain (nobody left to approve) | (a) admins approve; (b) refuse | (a) The request goes to the admins: any admin who is not the author can decide it. An emergency at the top of the organisation still has a path, and nobody is told "no" by the data model |
| Where overage is stored | (a) `ReviewSession.overage_minutes`; (b) a field on CapacityDay | (a) It is on the session that caused it, so it can be reported per reviewer, per team and per MR without a second source of truth. CapacityDay keeps counting the slot in `booked_minutes`, which may exceed the budget. That day then takes no normal booking, and other days are unaffected (URG-20) |
| Urgent merge routing over the daily limit | (a) yes, with `over_limit` recorded; (b) no | (a) yes, the earliest window from now, as you lean. Other merge rules still apply (URG-23) |
| Team limit: warn or block | (a) warn; (b) block | (a) warn: the tab shows it before the decision, and the admins get one alert per approval over the limit (URG-13). Blocking would stop a real emergency at the limit |
| The week for the limit | (a) calendar week; (b) rolling 7 local days | (b): no week-start convention needed (the company week starts on Saturday) |
| Delegate when the approver leads several teams | (a) the author's team's delegate first; (b) any | (a), then the lowest-id team the approver leads |
| Should an approval re-route an already-approved MR's later-day merge assignment? | (a) yes; (b) no | (a) yes, as part of URG-23. The open assignment is cancelled and a window from now is assigned |
