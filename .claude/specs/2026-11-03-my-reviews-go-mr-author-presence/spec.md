# SPEC: My reviews: Go to MR and author presence

Key: MRV · Route: project · Risk: high
Created: 2026-11-03

## Problem

On My reviews, a reviewer sees their booked reviews but has no way to jump to the MR in GitLab. The orchestrator does not store an MR's web address. A reviewer also cannot ask the author to join the review session. Today that happens in side chats that nobody tracks, and the author often finds out too late.

## Non-goals

- No GitLab comment or mention. Notifying the author goes through the existing chat notifier only (developer decision). `post_note` stays unimplemented.
- No meeting time or calendar invite. A booking has a day, not a time, and the request names that day.
- No acceptance or decline from the author. The author is told; nothing waits on their answer.
- No presence requests for MRs that are not booked to the requesting reviewer, and none from the author's side.
- No change to booking, handover or the AI gate.
- No backfill of web URLs for MRs stored before this change. They fill in on their next webhook or reconciliation.

## Acceptance criteria

| ID | Criterion | Class | Failure it catches |
|---|---|---|---|
| MRV-1 | WHEN a Merge Request Hook (`object_attributes.url`) or a reconciliation event (`web_url`) carries the MR's web address, THE MR lifecycle SHALL store it on the MR, replacing an older one. | INTEGRATION CONTRACT | The link is missing or stale after the MR moves to another project path |
| MRV-2 | IF a carried web address is not text, cannot be parsed, is not an absolute `http` or `https` URL, or is longer than 500 characters, THEN THE MR lifecycle SHALL not store it, SHALL keep the previous one, and SHALL process the rest of the event normally. | SECURITY BOUNDARY | A `javascript:` or relative value is rendered as a clickable link, or an unstorable value fails the whole event and blocks the MR for good |
| MRV-3 | WHEN a reviewer opens My reviews, THE panel SHALL show, for each booked review whose MR has a stored web address, a "Go to MR" link to that address that opens in a new tab without giving the new page access to the panel (`target="_blank"`, `rel="noopener noreferrer"`). A review whose MR has no address SHALL show no link. | CRITICAL BEHAVIOR | The reviewer cannot reach the MR, or the opened page can script the panel tab |
| MRV-4 | WHEN a reviewer requests the author's presence for one of their own booked reviews, with an optional note of at most 500 characters, THE panel SHALL store an active presence request, queue one chat notification to the MR's author naming the MR, the reviewer, the booked day and the note, and write an audit event. | CRITICAL BEHAVIOR | The author is not told, or the request leaves no trace |
| MRV-5 | WHILE a booked review has an active presence request, a further request for it SHALL store nothing and send nothing, and My reviews SHALL show "Author requested" with a "Cancel request" button instead of the request button. | BUSINESS RULE | A double click or a second visit spams the author |
| MRV-6 | WHEN the reviewer cancels an active presence request, THE panel SHALL mark it withdrawn, queue one chat notification to the author saying they are no longer needed, and write an audit event. | CRITICAL BEHAVIOR | The author still comes after the reviewer changed their mind |
| MRV-7 | IF a presence request or cancellation names a review that is not booked to the signed-in reviewer, or a note longer than 500 characters, THEN THE panel SHALL reply 404 (review not theirs or not booked) or 400 (note too long), and store and send nothing. | SECURITY BOUNDARY | A developer can make the orchestrator notify anyone, about any MR |
| MRV-8 | WHEN the author opens one of their MRs in My MRs, THE panel SHALL show each active presence request on it whose booked day is today or later, with the reviewer, the area, the booked day and the note. | CRITICAL BEHAVIOR | The author misses the request in chat and has nowhere to look it up, or is shown a request for a day that has passed |

Not tested, and why:
- The look of the buttons and the badge — checked by eye
- CSRF on the request and cancel forms — FRAMEWORK BEHAVIOR (Django middleware)
- Template auto-escaping of the note — FRAMEWORK BEHAVIOR (Django templates escape by default; no `|safe` is used)

## Invariants

- INV-1 — At most one active presence request per area review — owned by the database — enforced by a partial unique constraint on (area review) where the state is active, plus the panel checking before it inserts.
- INV-2 — A presence request, its notification and its audit event commit together — owned by `reviews.presence` — enforced by one `transaction.atomic` per request or cancellation, with the notification written to the outbox inside it.
- INV-3 — A reviewer can request presence only on reviews booked to them — owned by the panel views and `reviews.presence` — enforced by looking the area review up by id AND `reviewer = self.person` AND state booked.
- INV-4 — Only `http`/`https` absolute URLs of at most 500 characters are ever stored as MR web addresses, and the check never raises — owned by the lifecycle — enforced by one total check before storing.

## Contracts

- New model field `MergeRequest.web_url` (nullable), and a new model `PresenceRequest`: area review, requested by (Person), note, state (`active`, `withdrawn`), created at, withdrawn at. One `reviews` migration.
- `MergeRequestSummary` (the gateway's MR list item) gains `web_url`.
- New panel form posts: `/panel/reviews/<area_review_id>/presence/` (request, with an optional `note`) and `/panel/reviews/<area_review_id>/presence/cancel/`.
- The reconcile event payload gains `web_url`. The mock GitLab sends `object_attributes.url` in Merge Request Hooks and `web_url` in the MR API and MR list, as GitLab does.
- New setting `RMO_PRESENCE_NOTE_MAX_LENGTH = 500`.
- Notification texts (chat): request: "<reviewer> asks you to join the review of !<iid> <title> on <day>." plus the note; withdrawal: "<reviewer> no longer needs you at the review of !<iid> on <day>."

## Validation decisions

- Web address is an absolute http(s) URL — REQUIRED — lifecycle, before storing (MRV-2, INV-4).
- Note length ≤ `RMO_PRESENCE_NOTE_MAX_LENGTH` — REQUIRED — the form (MRV-7). Empty is allowed.
- Review booked to the signed-in reviewer — REQUIRED — the lookup (MRV-7, INV-3).
- Note content — NOT ADDED beyond length; rendered escaped, sent as plain chat text.

## Standards that apply

- python_code_style: Naming, Type annotations, Constants and module globals
- django_standards: Configuration and business constants, Django logic and style
- implementation_standards: Tests, Tests are DAMP, not DRY
- technical_standards: TS-5 (N+1 on My reviews)
- Checkpoint: `scripts/test.sh -q`

## Open questions

| Question | Options | Decision |
|---|---|---|
| Source of the MR link | Store GitLab's web_url / build from settings | Store `web_url` from webhooks and reconciliation (developer) |
| How the author is told | Chat + panel / + GitLab comment / chat only | Chat notification + shown on the author's MR page (developer) |
| What a request includes | Note, withdraw, one per booking | All three (developer) |
| What happens to an active request when the review is rebooked to someone else | Keep / withdraw | Out of scope: rebooking (handover) is not built. The request stays tied to its area review and its original reviewer |
| Author has no chat id | — | The notifier gets the author's GitLab user id, as every notification does today |
