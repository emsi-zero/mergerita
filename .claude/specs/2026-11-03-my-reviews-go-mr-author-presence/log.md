# LOG: My reviews: Go to MR and author presence

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-11-03 — spec created
Route: project · Key: MRV

## 2026-11-03 11:51 UTC — spec approved
sha256 3d5f80d68ada

## 2026-11-03 — Session 1 Gate B round 1 + spec delta
reviewer (spec, style, query): COMMENT — [Medium] no length limit on web_url; [Low] MergeRequestSummary.web_url not in Contracts; unused form placeholder; missing docstring. Query cost clean (3 / 4 queries).
deep-reviewer (logic, security): REQUEST_CHANGES — [High, confirmed, reproduced on Postgres] an over-long (>500), unparseable (`http://[::1`) or non-string web_url raised inside the event transaction, quarantining the event and blocking the MR for good (reconcile replays the same value). Presence path, scoping, locking (4-thread probe), escaping and rel all confirmed. Lows: stale reconcile snapshot overwrites a newer URL; no concurrency test for INV-1; request/cancel toggling can spam the author (accepted: audited, reach limited to own bookings); author sees past-day requests forever; real chat adapter must send plain text.
Spec delta (approved by the developer, "both"): MRV-2/INV-4 — non-text, unparseable, non-http(s) or >500-character addresses are ignored and never fail the event; MRV-8 — only requests for today or later are shown; Contracts list MergeRequestSummary.web_url.

## 2026-11-03 12:15 UTC — spec delta approved
sha256 91708fc7fd40

## 2026-11-03 — Session 1 Gate A
tests `scripts/test.sh -q` PASS (65 passed, 175 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace` PASS (8/8) · makemigrations --check clean. Mutation check: removing the presence row lock makes the concurrency test fail.

## 2026-11-03 — Session 1 Gate B
Delta re-review (deep-reviewer): High closed — the web_url check is total; every value that could raise is dropped, or rejected by Postgres jsonb at ingress (NUL, lone surrogate). MRV-1 holds on every non-stale reconcile path. Its new Low (concurrency test could not detect a removed lock) fixed by the main session: thread results asserted `[True, False]`, verified by mutation.
Accepted: a same-sha stale reconcile snapshot can briefly overwrite a moved MR's URL (heals at the next reconciliation); request/cancel toggling can send repeated chats to the author (audited, limited to the reviewer's own bookings). The real chat adapter must send plain text (docs/testing.md smoke item 15).
Session 1: done.

## 2026-11-03 12:23 UTC — spec closed
Session archived: /home/tabdeal/side-projects/merge-orchestrator/.claude/craft/archive/session-20261103T122309+0000.json
