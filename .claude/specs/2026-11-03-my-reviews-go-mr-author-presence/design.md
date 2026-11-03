# DESIGN: My reviews: Go to MR and author presence

Spec: spec.md · Key: MRV

## Shape

- The web address is domain data on `MergeRequest`, written by the lifecycle (the only writer of MR rows) from the hook and reconcile payloads.
- Presence requests are review-domain data: a new `PresenceRequest` model plus a small service, `reviews/presence.py` (`request_presence`, `withdraw_presence`). The service runs one atomic block, writes the row, the audit event and the outbox notification, and takes no transaction from its caller. This follows `reviews/availability.py`.
- The panel adds two POST views and template changes. It never writes models directly.

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| `MergeRequest.web_url = URLField(max_length=500, null=True, blank=True)`. `_record_web_url(mr, value)` in lifecycle stores the value only when `urlsplit(value).scheme in ("http", "https")` and `netloc` is set. It is called on open, on update hooks and on reconcile events | `reviews.lifecycle` | INV-4, MRV-1, MRV-2 | Building the URL from settings (paths drift) |
| `PresenceRequest(area_review FK, requested_by FK Person, note TextField(blank), state TextChoices active/withdrawn, created_at, withdrawn_at null)`. `UniqueConstraint(fields=["area_review"], condition=Q(state="active"), name="presence_one_active")` | `reviews.models` | INV-1 | A boolean on AreaReview (no history, no note) |
| `request_presence(reviewer, area_review_id, note, actor)`: `with transaction.atomic()` → `AreaReview.objects.select_for_update(of=("self",)).select_related("merge_request__author").get(pk=…, reviewer=reviewer, state=booked)` (DoesNotExist → the view returns 404). If an active request exists, return `False` and do nothing. Otherwise create the row, record audit (`presence.requested`, after = note and day), queue `notify` to the author, and return `True` | `reviews.presence` | INV-1, INV-2, INV-3, MRV-4, MRV-5 | Catching the IntegrityError only (the row lock plus the check give a clean no-op; the constraint is the backstop) |
| `withdraw_presence(reviewer, area_review_id, actor)`: the same lookup and lock; with no active request, do nothing; otherwise set withdrawn and `withdrawn_at`, record audit `presence.withdrawn`, queue `notify` | `reviews.presence` | INV-2, INV-3, MRV-6 | — |
| Panel: `RequestPresenceView` and `CancelPresenceView` (POST only, through `PanelAccessMixin`). `PresenceForm` (note: `CharField(max_length=settings.RMO_PRESENCE_NOTE_MAX_LENGTH, required=False)`). An invalid form re-renders My reviews with status 400. A valid post redirects to My reviews with a message | `panel` | MRV-7 | — |
| My reviews query: add `prefetch_related(Prefetch("presence_requests", queryset=…active…, to_attr="active_presence"))`. My MRs detail: prefetch the active requests through its area reviews | `panel.views` | TS-5 | One query per row |
| "Go to MR" link: `<a href="{{ mr.web_url }}" target="_blank" rel="noopener noreferrer">`, rendered only when `web_url` is set. Auto-escaping covers attribute quoting | `panel` templates | MRV-3 | — |
| Mock: `mr_hook` sets `object_attributes.url`, and the MR JSON and list set `web_url` = `<mock base>/group/project-<pid>/-/merge_requests/<iid>`. The base comes from the request's base URL (API) or from a `public_url` the app is created with (hooks; default `http://127.0.0.1:<port>`) | `gitlab_mock` | MRV-1 | — |
| Reconciler: `_payload` adds `web_url` from the list item | `reviews.reconciler` | MRV-1 | — |

## Data and migrations

One `reviews` migration:
- `AddField MergeRequest.web_url`, nullable, so a mixed-version deploy is safe.
- `CreateModel PresenceRequest` with its partial unique constraint.

Volume is a handful of rows a day. Rollback: reverse the migration.

## Standards review

| Standard | Verdict |
|---|---|
| django: Configuration and business constants | satisfied — the note max length is in settings |
| django: Transactions | satisfied — one atomic block per presence action, no nesting, no network (notifications go to the outbox) |
| django: thin views | satisfied — the views validate and call the service |
| django: FK DO_NOTHING + db_constraint=False | satisfied (PresenceRequest FKs) |
| TS-5 N+1 | satisfied — prefetches on both pages |
| Security | satisfied — the lookup is scoped to the reviewer (INV-3), http(s)-only URLs (INV-4), `rel=noopener noreferrer`, escaping |
| implementation: Tests | satisfied — one test per MRV criterion |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GitLab's hook field name differs (`url` vs `web_url`) | Low | Low — no link | Both are read; add to the smoke-test list |
| A rebooked review keeps an old request | Low (handover not built) | Low | Recorded in the spec's open questions |
