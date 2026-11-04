# SPEC: Review outcomes and continuity

Key: RVO · Route: project · Risk: high
Created: 2026-11-03

## Problem

An MR that reaches `in_review` never goes further. After a reviewer's booked slot, nothing records what happened, nothing books the next session when the reviewer needs more time, and nothing re-books when they hand the review back. GitLab approvals by human reviewers are never read, so no MR ever becomes approved. When a reviewer requests changes, the author's fix comes back through the AI gate and then waits forever, because area reviews already exist and nothing books a follow-up. This is flow 3 of the design (review session outcome), with the reviewer continuity of flow 2 and the follow-up of flow 6.

## Non-goals

- No merge routing or release. `approved` is the last state in this change (flows 7, 8).
- No handover package and no leave-triggered rebooking (flow 4). Hand back just re-books another owner.
- No continuation reserve in the daily budget, no wait clock or reminders, no reserve release (flow 6 beyond the follow-up).
- No missed-slot detection or watchdog. A session with no outcome stays pending.
- No chat buttons. Outcomes are reported in the panel only.
- No urgency (flow 5).
- No change to the AI gate rules (GLI-5..7, GLI-14).

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| RVO-1 | WHEN a booking is made (first booking, follow-up, more time or hand back), THE booking engine SHALL record it as a review session of that area review, with reviewer, day, slot minutes, reason and outcome `pending`; the area review's current reviewer and day are those of its latest session. | CRITICAL BEHAVIOR | Sessions are lost, so outcomes and continuity have nothing to attach to |
| RVO-2 | WHEN the reviewer marks their pending session Done, THE panel SHALL set the session's outcome to `done` and write an audit event, and SHALL NOT change the area review's or MR's approval. | BUSINESS RULE | A click in the panel approves an area without a GitLab approval |
| RVO-3 | WHEN the reviewer chooses Need more time on their pending session, THE booking engine SHALL set its outcome to `more_time` and book a new session with the same reviewer on the first working day after that session's day with enough free budget, marked as a continuation, and notify the reviewer; IF none fits within the horizon, THEN the area review SHALL become `unbooked` and an admin alert SHALL be queued. An active presence request on that area review SHALL be withdrawn, and the author told they are no longer needed. | BUSINESS RULE | The review stops, or moves to a different reviewer who has to start over |
| RVO-4 | WHEN the reviewer hands their pending session back, THE booking engine SHALL set its outcome to `handed_back` and book the area review with a different eligible owner (never the author, never the one handing back), marked as a continuation, set that reviewer in GitLab and notify them; IF none fits, THEN the area review SHALL become `unbooked` and an admin alert SHALL be queued. Other areas' reviews and approvals SHALL be unchanged. The handed-back session's minutes SHALL be given back to its day's budget when that day is today or later. An active presence request on that area review SHALL be withdrawn, and the author told they are no longer needed. | BUSINESS RULE | A hand back is lost, goes back to the same person, or resets approvals of other areas |
| RVO-5 | IF a Done, Need more time or Hand back request names a session that is not the signed-in reviewer's latest pending session of a booked area review, THEN THE panel SHALL reply 404 and change nothing; a session that already has an outcome SHALL accept no second outcome. | SECURITY BOUNDARY | A developer changes someone else's booking, or a double click books twice |
| RVO-6 | WHEN a Merge Request Hook, a Note Hook or a reconciliation event arrives for an MR in `in_review`, THE MR lifecycle SHALL queue one coverage check for that MR. | CRITICAL BEHAVIOR | Human approvals are never read, so no MR is ever approved |
| RVO-7 | WHEN the coverage check runs, THE MR lifecycle SHALL read from GitLab, for each booked or approved area review, its current reviewer's latest verdict time and then review state. A verdict is fresh only when it is newer than the MR's latest push (the later of its AI-gate entry time and its head-sha time, both set only by pushes and the AI gate). A fresh `approved` marks a booked area review `approved` and closes its pending session as `done`; an approved area review whose reviewer no longer shows a fresh `approved` goes back to `booked` and gets a follow-up session with the same reviewer, marked as a continuation, and that reviewer is notified; a fresh `requested_changes` records the change request's GitLab time on that area review (resetting its approval if it had one, without booking a follow-up), moves the MR to `waiting_on_author`, notifies the author, and keeps every reviewer; the AI Reviewer's approval then pulls the MR forward only when it is newer than every open change request on the MR. For an MR in `waiting_on_author` with no push since, WHEN every area review with an open change request shows a fresh `approved` newer than its own change request, THE MR SHALL move back to `in_review` (clearing those change requests) and the check continues; other areas' approvals are untouched. WHEN every area review of the MR is `approved`, THE MR SHALL move to `approved`. Stale or other states change nothing. Each change writes an audit event. | BUSINESS RULE | An MR is approved without every area's current approval, an approval from before a push counts, a human's change request is undone without a push, or changes requested by a human are ignored |
| RVO-8 | WHEN an MR that already has area reviews passes the AI gate again, THE booking engine SHALL book a follow-up session for each area review that is not `approved` — with the same reviewer, or, for an `unbooked` area review, with any eligible owner — marked as a continuation, and SHALL create no new area reviews. A session of that area review still `pending` at that moment SHALL be closed with outcome `superseded`. An area review whose reviewer GitLab no longer shows as freshly approved (by the RVO-7 freshness rule; for example approvals reset on push) SHALL lose its `approved` mark and get a follow-up too. The MR SHALL then move to `in_review` even when some follow-up found no fit (that area review stays `unbooked`, blocks approval, and an admin alert is queued), and a coverage check SHALL be queued at once. | BUSINESS RULE | A fixed MR waits forever, is reviewed from scratch by new people, or gets stuck in `awaiting_booking` when one area cannot be booked |
| RVO-9 | WHEN a reviewer opens My reviews, THE panel SHALL list first, under "Overdue", their pending sessions of booked area reviews whose day has passed, then each day's sessions with continuation sessions before the others; it SHALL show Done, Need more time and Hand back on each pending session, and show the outcome instead of the buttons once one is chosen. | CRITICAL BEHAVIOR | Follow-ups are buried behind new reviews, a missed session can never get an outcome, or a reviewer cannot report one |

Not tested, and why:
- The look of the outcome buttons — checked by eye
- CSRF on the outcome forms — FRAMEWORK BEHAVIOR
- GitLab's real reviewer-state and system-note texts — covered by the smoke checklist (GitLab spec), reused here

## Invariants

- INV-1 — An area review has at most one pending session, and while it is booked its current reviewer and day equal its latest session's (an unbooked area review has none) — owned by the booking engine — enforced by a partial unique constraint (one `pending` per area review) and by writing the session and the area review in one transaction.
- INV-2 — An area review is `approved` only after GitLab showed its current reviewer's fresh approval; an MR is `approved` only when all its area reviews are — owned by the coverage check — enforced by deriving both from GitLab reads under a row lock on the MR.
- INV-6 — Locks are always taken in the order MR → booking lock → area review → review session → capacity — owned by every `reviews` writer — enforced by code review and a two-thread test.
- INV-3 — No GitLab call inside a database transaction — owned by the coverage check — enforced by reading states and verdict times first, as the AI gate does.
- INV-4 — The author is never booked, and Hand back never re-books the same reviewer — owned by the booking engine — enforced by candidate exclusion plus a check before save.
- INV-5 — Outcome changes, bookings, audit events and outbox calls of one action commit together — owned by `reviews` services — enforced by one atomic block per action.

## Contracts

- New model `ReviewSession` (area review, reviewer, day, slot minutes, reason, `is_continuation`, outcome `pending|done|more_time|handed_back|superseded`, outcome at). Booked area reviews get a backfilled session in the migration. One `reviews` migration.
- `AreaReviewState` gains `approved`. `MergeRequestState` gains `approved`.
- New field `AreaReview.changes_requested_at` (nullable): the GitLab time of its reviewer's open change request; cleared by a push follow-up or a resume. One `reviews` migration.
- A follow-up sends one notification to its reviewer.
- New outbox kind `check_coverage`.
- New audit events: `area_review.unbooked` (before: reviewer, day; after: none) when a re-booking finds no fit; `area_review.approved` and `area_review.approval_reset` (before/after: state); `review_session.superseded` (after: outcome); `mr.changes_requested` and `mr.approved` (before/after: state).
- Follow-up notification to the reviewer: "Follow-up review of !<iid> booked on <day>."
- New panel form posts: `/panel/sessions/<id>/done/`, `/panel/sessions/<id>/more-time/`, `/panel/sessions/<id>/hand-back/`.
- `resolve_areas` books follow-ups for an MR that already has area reviews, instead of returning.
- Notification texts: more time ("Next session for !<iid> booked on <day>."), hand back to the new reviewer (the booking text, marked as a continuation), changes requested to the author ("<reviewer> requested changes on !<iid>.").

## Validation decisions

- The session is the reviewer's latest pending one, of a booked area review — REQUIRED — the scoped lookup (RVO-5, INV-1).
- Freshness of a human verdict — REQUIRED — the verdict system note is newer than the MR's latest push: `max(ai_gate_entered_at, head_sha_at)`.

## Standards that apply

- python_code_style: Naming, Type annotations, Modular design, Constants and module globals
- django_standards: Configuration and business constants, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- technical_standards: TS-5
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| When an area is covered | GitLab approval / panel Done | GitLab shows the current reviewer approved, freshly (developer) |
| Human requests changes | Back to author then same reviewer / later | Back to author; after the AI gate, follow-ups with the same reviewer (developer) |
| Hand back | Rebook another owner / unbook and alert | Rebook another eligible owner as a continuation (developer) |
| Need more time | Same reviewer next fitting day / with reserve | Same reviewer, first fitting day after the session's day; no reserve (developer) |
| Is an old reviewer removed from GitLab after a hand back? | Remove / keep | Keep: removing a reviewer is a new GitLab write; the new reviewer is added |
| Can Done be pressed before the booked day? | Yes / no | Yes; the button acts on the pending session whatever its day |
| Follow-up while a session is still pending | Supersede / block | Close it as `superseded` (developer, plan review) |
| Which outcomes give minutes back | — | Hand back and superseded, for days from today on; Done and Need more time keep them (developer, plan review) |
