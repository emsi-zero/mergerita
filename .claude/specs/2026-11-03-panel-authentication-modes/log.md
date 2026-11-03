# LOG: Panel authentication modes

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-11-03 — spec created
Route: project · Key: PAU

## 2026-11-03 10:36 UTC — spec approved
sha256 b99dcf910fd5

## 2026-11-03 — Session 1 Gate A
tests `scripts/test.sh -q` PASS (56 passed, 149 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace` PASS (6/6). Manual: `PANEL_AUTH_MODES=bogus manage.py check` refuses to start.

## 2026-11-03 — Session 1 Gate B
reviewer (spec, style): COMMENT — [Medium] PAU-6 tested the parser only → subprocess start-up subTest added; Lows (400 on failed login → 200; duplicate constant; docstrings).
deep-reviewer (logic, security): REQUEST_CHANGES — [High, confirmed, reproduced] a panel session key sent as `sessionid` opened the admin (and the reverse): both areas share one session table → sessions carry an area marker (`_rmo_area`), and a mismatch gets an empty session. [Medium] `next=/panel/../admin/` escaped the prefix → normalized (unquote + normpath) before the check. [Low] `sensitive_post_parameters` and `never_cache` added. Root-deployment assumption recorded in design.md.
Delta re-review: High and Medium closed (probe re-run, both directions); INV-1..3 hold; APPROVE. Its Low (dot-segment `next` subtests missing) fixed by the main session.
Deferred: every reply now carries `Vary: Cookie` because the middleware reads the marker on each request (no cache in front; accepted). `next` query strings are dropped (no panel view reads GET).
Session 1: done.

## 2026-11-03 11:26 UTC — spec closed
Session archived: /home/tabdeal/side-projects/merge-orchestrator/.claude/craft/archive/session-20261103T112654+0000.json
