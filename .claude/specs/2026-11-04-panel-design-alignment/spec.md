# SPEC: Panel design alignment

Key: PNS · Route: task · Risk: low
Created: 2026-11-04

Risk is low: no money, no new write path, no migration. The MR detail keeps its existing author scoping (DVP access rules).

## Problem

The developer panel has drifted from the approved design (the "Developer panel" design canvas, Tabdeal palette) and is inconsistent with itself. My MRs is two pages where the design has one, with the list and the selected MR side by side. The header lacks the person's name, areas and tab counts. The detail has no "Open in GitLab". Pages also mix two button styles (`.primary`/`.remove` next to `.btn`), two field styles, a browser-native progress bar, a double border on My merges, different empty states, and a font stack without IRANSans.

## Non-goals

- No new features from the design that the app does not have (the "Request urgent review" button, the leave hand-off note, live saving of availability without a Save button).
- My reviews keeps its newer layout (developer decision); only its sizes, spacing and colours are aligned.
- No change to the admin site.
- No change to what any page queries beyond the counts and the detail-with-list below.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| PNS-1 | WHEN a developer opens My MRs, THE panel SHALL show their MR list and, beside it, the detail of the selected MR: the one in the URL (`/panel/mrs/<id>/`), else their most recent MR. The selected card SHALL be marked, and the page head SHALL read "<N> open · <M> need your action", where open excludes merged MRs and "need your action" counts MRs waiting on the author. Another developer's MR id SHALL still answer 404. | CRITICAL BEHAVIOR | The developer has to switch pages to see an MR's state, or sees another developer's MR |
| PNS-2 | WHEN a linked developer opens any panel page, THE header SHALL show their name with initials and the code areas they own, and the tabs SHALL show counts: My MRs = open MRs, My reviews = pending review sessions up to the booking horizon (overdue included), My merges = open merge assignments (merge owners only). A zero count SHALL not be shown. | BUSINESS RULE | The header shows a login name instead of the person, or counts that disagree with the pages |
| PNS-3 | WHEN the MR detail is shown and GitLab's address of the MR is known, THE panel SHALL show an "Open in GitLab" link opening it in a new tab, and the project path taken from that address; without the address, neither is shown. | CRITICAL BEHAVIOR | The developer cannot get from the panel to the MR in GitLab |

Not tested, and why (checked by eye against the design canvas):
- One button system (`.btn` primary, ghost, ghost-danger) and one field style on every page, including sign-in and availability.
- A styled booked-minutes bar (track and fill in the palette, red when over budget) instead of the browser's progress bar.
- My reviews: a clear gap between the "Request author" row and the Done / Need more time / Hand back row (they touch today).
- Consistent page heads, card padding, section spacing and empty states; no card inside a card on My merges.
- Tab hover and keyboard focus styles; 40 px tab and 44 px pill targets as in the design.
- Font stack `Roboto, IRANSans, system-ui, sans-serif`; duplicate CSS rules removed.
- Layout at phone width: the MR detail stacks under the list.

## Invariants

- INV-1 — Every panel query is scoped to the signed-in Person (existing DVP INV-1) — owned by `panel.views` — enforced by filtering on `self.person` (and in the header, `request.user.person`) in every query, including the new counts and the list beside the detail.

## Contracts

- `/panel/mrs/` and `/panel/mrs/<id>/` render the same page (list + detail). URLs and names are unchanged.
- A panel context processor adds the header person, areas and tab counts on panel pages only.
- `panel/mr_detail.html` is folded into `panel/mrs.html` (with an included `_mr_detail.html`).

## Validation decisions

- None new.

## Standards that apply

- python_code_style: Naming, Type annotations
- django_standards: Django logic and style
- technical_standards: TS-5 (no N+1 on the list and the counts)
- implementation_standards: Tests
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| My MRs layout | One page, list + detail / two pages | One page, as in the design (developer) |
| My reviews layout | Keep the newer restyle / back to the design | Keep the restyle, align sizes and colours (developer) |
