# TASKS: My reviews: Go to MR and author presence

Spec: spec.md · Key: MRV · Route: project · State: done
Checkpoint: `scripts/test.sh -q`

## Session 1 — MR web address, Go to MR, author presence requests

State: done · Covers: MRV-1, MRV-2, MRV-3, MRV-4, MRV-5, MRV-6, MRV-7, MRV-8

- [x] T1 — `reviews/models.py`: add `MergeRequest.web_url` and the `PresenceRequest` model with its partial unique constraint. Add one migration. Add `RMO_PRESENCE_NOTE_MAX_LENGTH = 500` to settings — MRV-1, INV-1
- [x] T2 — `reviews/lifecycle.py`: add `_record_web_url` (http/https only), called on open, update and reconcile. `reviews/reconciler.py`: add `web_url` to the payload — MRV-1, MRV-2
- [x] T3 — `gitlab_mock/app.py`: send `object_attributes.url` in MR hooks, and `web_url` in MR JSON and the MR list. Add a `public_url` option and the CLI flag `--public-url` — MRV-1
- [x] T4 — `reviews/presence.py`: `request_presence` and `withdraw_presence`, with locking, audit and outbox notify — MRV-4, MRV-5, MRV-6, INV-2, INV-3
- [x] T5 — `panel/forms.py` `PresenceForm`. `panel/views.py`: `RequestPresenceView` and `CancelPresenceView`. `panel/urls.py`: add the two routes — MRV-4, MRV-6, MRV-7
- [x] T6 — Templates:
  - `panel/reviews.html`: the "Go to MR" link (new tab, `rel=noopener noreferrer`); a "Request author" form with an optional note; and, while a request is active, the "Author requested" badge with a "Cancel request" form.
  - `panel/mr_detail.html`: the list of active presence requests.
  - Prefetches in both views.

  — MRV-3, MRV-5, MRV-8
- [x] T7 — Tests, one per criterion:
  - `reviews/tests/test_lifecycle.py` or a new `reviews/tests/test_web_url.py`: MRV-1, MRV-2.
  - `panel/tests/test_reviews.py`: MRV-3.
  - `panel/tests/test_presence.py`: MRV-4, MRV-5, MRV-6, MRV-7, MRV-8.
  - Add the two new POST URLs to the access tests (DVP-1/DVP-2 POST loop).
- [x] T8 — `docs/testing.md`: a new scenario for Go to MR and presence requests. Add "Merge Request Hook carries `object_attributes.url`; MR list carries `web_url`" to the smoke checklist

Gate A: `bin/craft-spec trace --session 1` green · checkpoint green through `bin/craft-check` · pylint, isort, black green
Gate B: `reviewer` (lenses: spec, style, query) · `deep-reviewer` (logic, security on INV-1..4, the scoped lookup, URL validation, the new-tab link)

Resume: Session 1 code, tests and docs are written; all Gate A checks exit 0. Next: Gate B review, then `/finish`.
