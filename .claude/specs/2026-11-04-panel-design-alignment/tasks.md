# TASKS: Panel design alignment

Spec: spec.md · Key: PNS · Route: task · State: done
Checkpoint: `scripts/test.sh -q`

## Session 1 — Panel design alignment

State: done · Covers: PNS-1, PNS-2, PNS-3

- [x] T1 — `panel/views.py`: My MRs list + selected detail on one page (both URLs); counts in the page head; project path from `web_url` — PNS-1, PNS-3
- [x] T2 — `panel/context_processors.py` + settings: header person, areas, tab counts — PNS-2
- [x] T3 — Templates: `mrs.html` with `_mr_detail.html` (drop `mr_detail.html`), `base.html` header and tab counts, buttons/fields on login and availability, My merges without nested cards, empty states
- [x] T4 — `panel/static/panel/panel.css`: one button and field system, styled bar, spacing, tabs, fonts, My reviews row gap, phone layout, remove duplicates
- [x] T5 — Tests: PNS-1, PNS-3 in `panel/tests/test_merge_requests.py`; PNS-2 in `panel/tests/test_header.py`

Gate A: `bin/craft-spec trace` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query)

Resume: Done. Committed.
