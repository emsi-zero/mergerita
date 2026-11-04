# LOG: Panel design alignment

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-11-04 — spec created
Route: task · Key: PDA

## 2026-11-04 12:42 UTC — spec approved
sha256 c8f1b7a20ac8

## 2026-11-04 — spec approved
Developer: "yeah" plus one addition: the gap between the presence row and the outcome buttons on My reviews (added to the checked-by-eye list before approval).

## 2026-11-04 — Gate A
tests `scripts/test.sh -q` PASS (99 passed, 289 subtests) · pylint 10.00 PASS · isort PASS · black PASS · makemigrations --check clean · `craft-spec trace` PASS (3/3). Headless screenshots could not run (snap Firefox cannot read the job directory); the look is checked against the mockup markup and by the developer in the browser.

## 2026-11-04 — Gate B
reviewer: APPROVE — PNS-1..3 implemented and tested; INV-1 holds (every header and My MRs query is scoped to the person; another developer's MR is 404); the header adds 3 queries per panel page (4 for a merge owner), no N+1; CSS and templates match the mockup on every checked-by-eye item.
Low fixed: the header context processor keys on the `panel` URL namespace, not a hardcoded path. Low accepted: the open-MR count is queried again on My MRs (one indexed COUNT).
Intended differences from the mockup: 38 px buttons (one control height), "Done" uses the primary button, My reviews keeps its divider (developer decision).
Gate A after the fix: tests PASS (99 passed) · pylint 10.00 · isort · black · trace 3/3. Done.
