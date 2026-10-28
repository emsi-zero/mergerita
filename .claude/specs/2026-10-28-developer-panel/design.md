# DESIGN: Developer panel

Spec: spec.md · Key: DVP

## Shape

- A new Django app, `panel`, holds the presentation only: views, forms, URLs, templates, and one static CSS file. It owns no models. It reads `reviews` and `audit` models and calls one new `reviews` service for writes, so the dependency is one-way: `panel → reviews, audit`. This keeps template and form code out of the domain app.
- Availability writes live in `reviews/availability.py`, because they change domain data (`Person`, `Leave`) that booking reads. They also write audit events. The panel never writes models directly.
- Every view goes through one mixin, `PanelAccessMixin`. It applies `LoginRequiredMixin`, resolves `request.user.person`, renders 403 when the user has no Person, and puts the Person on `self.person`. Every query filters on `self.person` (INV-1).

## Decisions

| Decision | Layer that owns it | Invariant it protects | Alternative rejected |
|---|---|---|---|
| `Person.user = OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=DO_NOTHING, db_constraint=False, related_name="person")` | `reviews.models` | DVP-2, INV-1 | Matching users to people by username (fragile, spoofable) |
| One `PanelAccessMixin` for every panel view: login, then Person lookup, then 403 with the `panel/not_linked.html` template | `panel.views` | DVP-1, DVP-2 | A check in each view |
| MR detail: `MergeRequest.objects.filter(author=self.person)` then `get_object_or_404(pk=…)` | `panel.views` | DVP-4, INV-1 | Fetching by pk, then comparing |
| History: audit events whose subject is the MR, or one of its area reviews (`subject_type` + `subject_id` in one `Q`), ordered by `created_at`, shown as time plus a readable action label from a dict | `panel.views` | DVP-4 | A separate history table |
| Next-step text and badge label per state: a dict in `panel/views.py`, copied from the mockup | `panel.views` | DVP-3, DVP-4 | Text in the model |
| Size warning: `changed_lines > min(size_limit_lines)` of the areas across the MR's area reviews | `panel.views` | DVP-5 | Storing a flag |
| My reviews: `AreaReview.objects.filter(reviewer=person, state=booked, booked_day__range=(today, today + horizon - 1))`, `select_related("merge_request__author")`, `prefetch_related("areas")`. `CapacityDay` rows for those days are read in one query. The budget falls back to the person's current budget (the same rule booking uses) | `panel.views` | DVP-6, TS-5 | One query per day |
| Availability writes: `reviews.availability.set_working_days`, `set_daily_budget`, `add_leave` and `delete_leave`. Each takes `(person, …, actor)`, runs one `with transaction.atomic()`, writes the change and one `audit.services.record`, and returns nothing. `delete_leave` filters on `person` and raises `Leave.DoesNotExist` for another person's leave, which the view turns into 404 | `reviews.availability` | INV-1, INV-2, DVP-9, DVP-10 | Writing from the views |
| Forms: plain `django.forms.Form` classes. `WorkingDaysForm` is a `MultipleChoiceField` over the weekdays, required. `BudgetForm` is a `TypedChoiceField` from `RMO_DAILY_BUDGET_CHOICES`. `LeaveForm` is two dates plus a `clean()` that checks end ≥ start | `panel.forms` | DVP-7, DVP-8, DVP-9 | Model forms (they expose fields the user must not set) |
| Post/redirect/get: a valid post redirects to the Availability page with a success message (`django.contrib.messages`). An invalid post re-renders the page with errors and status 400 | `panel.views` | — | JSON endpoints |
| The audit actor is `f"user:{request.user.username}"` | `panel.views` | DVP-10 | A user id (less readable in admin) |
| Look: `panel/static/panel/panel.css`, with the Tabdeal palette as CSS custom properties on `:root`, the Roboto font, and the mockup's layout (header with tabs, cards, badges, pills). One `panel/base.html` | `panel` templates | — | Inline styles (the mockup format) |
| Login: `django.contrib.auth.urls` under `/accounts/`, plus a `registration/login.html` styled like the panel. `LOGIN_URL = "/accounts/login/"`, `LOGIN_REDIRECT_URL = "/panel/"` | `orchestrator.urls`, `panel` templates | DVP-1 | A custom login view |

## Data and migrations

`reviews` gets one migration: it adds the nullable, unique `Person.user`. The table is small (people), and the column is nullable with no default, so the migration is instant and needs no backfill. Admins link users in the Person admin, where `user` is added to the editable fields.

Rollback: revert the migration (drop the column) and remove `panel` from `INSTALLED_APPS`.

## Standards review

| Standard | Verdict |
|---|---|
| django: Configuration and business constants | satisfied — budget choices and login URLs in settings (S1-T1) |
| django: thin views | satisfied — views query and render; writes go through `reviews.availability` (S2-T2) |
| django: Transactions | satisfied — one `atomic` per availability change, no nesting, no network (S2-T1) |
| django: FK `DO_NOTHING` + `db_constraint=False` | satisfied (S1-T1) |
| django: i18n | at risk — user-facing panel text is English literals → accepted for this version (non-goal: no Persian/RTL); text lives in templates, so `{% translate %}` can wrap it later without code changes |
| python: Modular design | satisfied — `panel` is an entry-point app with no data; one-way dependency |
| TS-5 N+1 | satisfied — list, detail and reviews use `select_related`/`prefetch_related`; capacity in one query (S1-T4, S1-T5, S2-T3) |
| implementation: Tests | satisfied — one test per DVP criterion, Django `TestCase` with the test client |

Completeness:
- Models and migration: S1.
- Service: S2.
- Endpoints: S1 and S2.
- Error handling: 403, 404, 400 with form errors.
- Concurrency: N/A, single-row updates per person.
- Validation: the forms.
- Tests: one per criterion.
- Feature flag: N/A.
- Logging: N/A.
- Rollback: above.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A developer reaches another person's data through an id in the URL | Low | High | Every lookup is scoped to `self.person`; DVP-4 and DVP-9 test the 404 |
| A budget change lowers capacity below already booked minutes | Medium | Low | Existing bookings stay (spec); the reviews page shows booked over budget plainly |
| Django login is replaced by GitLab SSO later | Certain | Low | Only the login template and `LOGIN_URL` change; the mixin stays |
