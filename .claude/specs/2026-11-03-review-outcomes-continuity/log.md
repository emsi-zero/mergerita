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
