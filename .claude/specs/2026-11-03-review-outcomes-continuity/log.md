# LOG: Review outcomes and continuity

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-11-03 — spec created
Route: project · Key: RVO

## 2026-11-03 — spec delta during planning
RVO-8: a still-pending session is closed as `superseded` when a follow-up is booked. RVO-4 and superseded give minutes back for days from today on. Contracts: new outcome `superseded`. Approved by the developer.

## 2026-11-03 13:38 UTC — spec approved
sha256 d3f9ad747108

## 2026-11-03 — Session 1 Gate B round 1 + spec delta
reviewer (spec, style, query): COMMENT — INV-1 wording vs cleared cache on unbook; undocumented `area_review.unbooked`; `kind` naming; `type(self).outcome`; three subqueries (Low, accepted).
deep-reviewer (logic, security): COMMENT, no Critical/High — first booking unchanged; outcome races serialized (5 pairs, probe); no lock cycle with resolve_areas. [Medium] no test that hand back never books the author. Lows: overdue pending sessions have no recovery path; old-code bookings during deploy get no session (run migration with the worker stopped); active presence requests survive hand back / more time.
Main session: one intermittent failure of the GLI-13 e2e subtest in the full suite (passes 3/3 alone, 2/2 full re-runs). Likely cause: the mock's push and the bot's approval landing in the same millisecond, so the approval is (correctly) not newer than the push → fix the mock to give strictly increasing timestamps.
Spec delta (approved by the developer): RVO-9 lists overdue pending sessions first; RVO-3/RVO-4 withdraw an active presence request and tell the author; INV-1 allows an empty cache while unbooked; Contracts list `area_review.unbooked`.

## 2026-11-03 14:17 UTC — spec delta approved
sha256 0e166a3fdffa

## 2026-11-03 — Session 1 Gate A
tests `scripts/test.sh -q` PASS on 5 consecutive full runs (71 passed, 204 subtests; the GLI-13 e2e stable after the mock's strictly increasing timestamps) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 1` PASS (6/6) · makemigrations --check clean.

## 2026-11-03 — Session 1 Gate B
reviewer: COMMENT; deep-reviewer: COMMENT, no Critical/High (probes: first booking unchanged, 5 outcome race pairs serialized, no lock cycle). Fixed: hand-back-never-books-author test; overdue section (delta); presence withdrawn on hand back / more time (delta); `area_review.unbooked` asserted and in Contracts; INV-1 wording; naming and docstrings; mock timestamps strictly increasing. No re-review needed (no Confirmed Critical/High).
Accepted: three correlated subqueries on My reviews (one query, small set); old-code bookings during deploy get no session (run 0008 with the worker stopped — recorded in design.md).
Session 1: done.

## 2026-11-03 — Session 2 Gate B round 1 + spec delta
reviewer (spec, style, query): COMMENT — unlisted audit events and follow-up text; unbooked-area follow-up and immediate coverage check not in the spec; Lows: N+1 on reviewer in `_follow_up`, duplicated `APPROVED_STATE`, `asked_changes` naming. GitLab calls bounded (2 per booked area review).
deep-reviewer (logic, security): REQUEST_CHANGES. [High, confirmed, reproduced] a human change request is undone without a push: the AI bot's earlier approval stays fresh, so reconcile's waiting_on_author safety net re-books the MR and it loops. [High, confirmed, reproduced] a push while in_review does not move ai_gate_entered_at, so an approval from before the push counts. [Medium, reproduced] approved areas are never re-read, so a revoked approval still counts. [Medium, reproduced] one failed follow-up leaves the MR stuck in awaiting_booking. [Low] an approved area keeps a pending session nobody can close.
Spec delta (approved by the developer): RVO-7 — freshness against max(ai_gate_entered_at, head_sha_at); approved areas re-read and reset; requested changes also reset the AI-gate time; approval closes the pending session as done. RVO-8 — unbooked areas get any eligible owner; MR moves to in_review even with an unbooked area; coverage queued at once. Contracts list the audit events and follow-up text.

## 2026-11-03 14:48 UTC — spec delta approved
sha256 d9a126c85193

## 2026-11-04 — Session 2 delta re-review + spec delta
deep-reviewer delta re-review: all 5 round-1 findings closed (probes P1–P4). New: [High, confirmed, reproduced] an approval reset by coverage leaves the area booked with no session and no notification, so the MR waits forever. [Medium, reproduced] lock-order deadlock between coverage/supersede (area review → session) and the outcome lookup (session → area review). [Medium] a reviewer approving after requesting changes, with no push, leaves the MR in waiting_on_author.
Spec delta (approved by the developer, "both"): RVO-7 — a reset approval books a same-reviewer continuation and notifies; a fresh approval from the reviewer who requested changes, with no push, moves the MR back to in_review; the change-request reset uses the request's GitLab time (wording aligned with the code). RVO-8 — follow-up uses the RVO-7 freshness rule. New INV-6 — one lock order everywhere.

## 2026-11-04 07:15 UTC — spec delta approved
sha256 1c7d0f1f2ed4

## 2026-11-04 — Session 2 second delta re-review + spec delta
deep-reviewer: the reset-approval High and both Mediums closed (P5–P7, both deadlock races re-run); no lock cycle, no GitLab call inside transactions, no coverage loop. New [Medium, reproduced]: moving ai_gate_entered_at on a change request made latest_push() treat it as a push — a resume reset other reviewers' approvals (P8) and could drop a second reviewer's change request (P9). Lows: a change request on an approved area booked a follow-up before the fix (P11); unbooked follow-ups sent two notifications (P12). Audit-log lookup of the requester judged fragile.
Spec delta (approved by the developer): change requests are tracked per area review (`changes_requested_at`, GitLab time); ai_gate_entered_at is moved only by pushes and the AI gate; the AI safety net needs a verdict newer than every open change request; resume requires every open change request to be freshly approved; no follow-up until the push; one notification per follow-up.

## 2026-11-04 07:51 UTC — spec delta approved
sha256 f4c810d48b53

## 2026-11-04 — Session 2 Gate A
tests `scripts/test.sh -q` PASS on two full runs (75 passed, 231 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace` PASS (9/9) · makemigrations --check clean.

## 2026-11-04 — Session 2 Gate B
Four deep-review rounds. Confirmed Highs, each fixed and verified closed by delta re-review with probes: change request undone by the AI safety net; approval from before a push counted; reset approval with nobody assigned; resume past an AI fail after a push (P13). Mediums closed: approved areas re-read; partial follow-up stuck; lock-order deadlock (INV-6, two-thread test); approval after changes with no push; change requests tracked per area review (P8, P9). Lows closed: follow-up booked before the fix; double notifications. Final delta check: APPROVE, no findings.
Accepted / notes: coverage takes the global booking lock on every run (serializes with bookings, by INV-6); the clear-on-push is defence in depth (the resume condition alone blocks P13, tested by P13b); a late distinct older push hook can still reopen the gate (pre-existing).
Pending documentation delta (developer to approve): Contracts should also list the audit actions `area_review.changes_requested` and `review_session.done`.
Session 2: done. Project: all sessions done.
