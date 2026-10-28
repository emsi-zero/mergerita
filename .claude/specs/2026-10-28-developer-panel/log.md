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
