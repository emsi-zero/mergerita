# LOG: Developer panel

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-10-28 — spec created
Route: project · Key: DVP

## 2026-10-28 12:30 UTC — spec approved
sha256 7041610f73ba

## 2026-10-28 — Session 1 Gate A
tests `scripts/test.sh -q` PASS (27 passed, 42 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 1` PASS (5/5)

## 2026-10-28 — Session 1 Gate B
reviewer (spec, style, query): COMMENT, no Critical/High. deep-reviewer (logic, security): COMMENT, no Critical/High.
Fixed: [Medium] `django.contrib.auth.urls` exposed password reset/change → only login and logout routed, 404 asserted; [Medium] DVP-4 test lacked a decoy on another MR's area review → added; [Medium] audit (subject_type, subject_id) index added; [Low] invented "under 20 minutes" removed; splitting hint only with the size warning; header count wording; typing and docstrings.
Decision (developer): login has no attempt limit — accepted; the panel runs only inside the company network until GitLab SSO replaces the password login. Recorded in README.
Deferred: Google Fonts loaded from the client (internal tool, offline falls back to system fonts); `Person.user` left dangling if a Django user is deleted (DO_NOTHING by standard); view attributes set on model instances (refactor not shorter).
No re-review: no Confirmed Critical/High.
Session 1: done.

## 2026-10-28 — Session 2 Gate A
tests `scripts/test.sh -q` PASS (32 passed, 60 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace` PASS (10/10 all sessions)

## 2026-10-28 — Session 2 Gate B
reviewer (spec, style, query): COMMENT, no Critical/High. deep-reviewer (logic, security): COMMENT, no Critical/High.
Fixed: [Medium] POST endpoints had no anonymous/unlinked access tests → subTests in DVP-1/DVP-2 assert redirect/403 and no change; [Medium] budget rule duplicated → `reviews.booking.person_budget` shared; [Medium] hardcoded team default days → built from `default_working_days()`; [Low] My reviews showed the budget stored at booking time → live budget, no-row day tested; [Low] full day shown as over → only `booked > budget`; [Low] audit `before` read outside the lock → `select_for_update` re-read; [Low] concurrent leave deletes → `select_for_update`; [Low] docstrings.
Accepted (reviewer judged, spec-consistent): only days with bookings listed; past leave listed; mockup handover notice dropped (non-goal); tests share builders from test_merge_requests.py.
No re-review: no Confirmed Critical/High.
Session 2: done. Project: all sessions done.

## 2026-11-02 08:54 UTC — spec closed
Session archived: /home/tabdeal/side-projects/merge-orchestrator/.claude/craft/archive/session-20261102T085450+0000.json
