# Testing the orchestrator prototype

There are two ways to test:

1. **Automated tests.** One command runs a test for every acceptance criterion.
2. **Manual scenarios.** You run the mock GitLab (`scripts/gitlab-mock`), open and approve MRs in it, run the worker, and check the results in the admin, the API and the mock's state.

The mock GitLab sends real webhooks to the orchestrator, and the orchestrator reads and updates the MRs in the mock through the real GitLab gateway. The chat notifier is still a fake that lives in memory, so to see the chat messages you run the worker inside `manage.py shell`.

---

## 1. One-time setup

```bash
cd ~/side-projects/merge-orchestrator

# Python environment. It already exists in this checkout; on a fresh clone:
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Without python3-venv (Debian/Ubuntu): sudo apt install python3.12-venv, then the line above.
```

## 2. Automated tests

```bash
scripts/test.sh -q
```

Every test must pass. The run includes `tests_e2e/`, which drives the orchestrator and the mock GitLab together. The script also starts the local Postgres container `rmo-test-pg`, which the manual scenarios use too. Tests run in a separate `test_orchestrator` database, so they never touch your manual data.

To see which test proves each criterion:

```bash
~/.claude/plugins/cache/craft/craft-workflow/0.6.0/bin/craft-spec trace
```

## 3. Start the app for manual testing

Use four terminals. In each one, set the environment first:

```bash
cd ~/side-projects/merge-orchestrator
export DEBUG=1 GITLAB_WEBHOOK_TOKEN=dev-token DB_HOST=$PWD/.pgsock
export RMO_GITLAB_GATEWAY=integrations.gitlab.GitLabRestGateway
export GITLAB_URL=http://127.0.0.1:8080 GITLAB_TOKEN=mock-token
export RMO_AI_REVIEWER_GITLAB_USER_ID=90      # the AI Reviewer bot in the mock
```

With `DEBUG=1`, an `http` `GITLAB_URL` is allowed.

**Terminal 4, the mock GitLab.** It sends its webhooks to the server in Terminal 1, with the same secret:

```bash
.venv/bin/python scripts/gitlab-mock serve --port 8080 --token mock-token \
  --webhook-url http://127.0.0.1:8000/webhooks/gitlab/ --webhook-secret dev-token
```

**Terminal 1, once, then the server:**

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser        # e.g. admin / admin
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/admin/ and log in.

**Terminal 2, seed the people and areas (run once):**

```bash
.venv/bin/python manage.py shell <<'EOF'
from reviews.models import Person, CodeArea, Ownership
author = Person.objects.create(gitlab_user_id=11, name="Ali (author)")
sara = Person.objects.create(gitlab_user_id=21, name="Sara (api primary)")
reza = Person.objects.create(gitlab_user_id=22, name="Reza (api backup, web primary)")
Person.objects.create(gitlab_user_id=99, name="Admin", is_admin=True)
api = CodeArea.objects.create(name="api", path_patterns=["api/*"], size_limit_lines=400)
web = CodeArea.objects.create(name="web", path_patterns=["web/*"], size_limit_lines=400)
infra = CodeArea.objects.create(name="infra", path_patterns=["infra/*"], size_limit_lines=400)
Ownership.objects.create(person=sara, area=api, role="primary")
Ownership.objects.create(person=reza, area=api, role="backup")
Ownership.objects.create(person=reza, area=web, role="primary")
Ownership.objects.create(person=author, area=infra, role="primary")   # only the author owns infra
print("seeded")
EOF
```

You can also create these rows in the admin, under People, Code areas and Ownerships.

**Terminal 2, define the helpers:**

```bash
gmock() { .venv/bin/python scripts/gitlab-mock "$@"; }   # control the mock GitLab
post() {   # post <uuid> <json-body> [token]
  curl -s -w " -> HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/webhooks/gitlab/ \
    -H "Content-Type: application/json" -H "X-Gitlab-Token: ${3:-dev-token}" \
    -H "X-Gitlab-Event: Merge Request Hook" -H "X-Gitlab-Event-UUID: $1" -d "$2"
}
open_mr() {  # open_mr <iid>: a hand-made open webhook, for scenario A only
  post "open-$1" "{\"project\":{\"id\":7},\"object_attributes\":{\"iid\":$1,\"action\":\"open\",\"author_id\":11,\"title\":\"MR $1\"}}"
}
```

In the scenarios, `gmock open 7 11 ...` opens an MR in project 7 for author 11. The mock numbers the MRs of a project 1, 2, 3, ... in the order you open them. `gmock approve 7 <iid> 90` makes the AI Reviewer (user 90) approve, and `gmock request-changes 7 <iid> 90` makes it ask for changes. `gmock state` prints the mock's MRs, reviewers and webhook deliveries.

The orchestrator counts an AI Reviewer verdict only when the reviewer's latest verdict note (GitLab's system note "approved this merge request" or "requested changes") is newer than the time the MR entered the AI gate (its creation time, or the time of the last push), both read from GitLab's clock. The mock writes that system note for each `approve` and `request-changes`.

**Terminal 3, the worker.** Every time a scenario says "run the worker", use this block:

```bash
sleep 3   # the worker ignores events younger than 2 s
.venv/bin/python manage.py shell <<'EOF'
from django.core.management import call_command
from integrations.adapters import get_notifier
call_command("run_worker", "--once")
for call in get_notifier().calls: print("Chat:", call)
EOF
gmock state    # the reviewers that the mock GitLab now lists per MR
```

To see the result of each scenario:

- **API:** http://127.0.0.1:8000/api/merge-requests/<id>/ (log in to the admin first).
- **Admin:**
  - *Merge requests* and *Area reviews*: state, reviewer, day and reason.
  - *Outbox calls*: `done`, `pending` or `failed`, with attempts.
  - *Inbox events*: `processed_at`, `failed_at` and `last_error`.
  - *Audit events*: every state change, with before and after.

---

## 4. Scenarios

Each scenario uses a new MR `iid`, so they don't interfere with each other. The expected results below were checked against the running app on 2026-10-26, a Monday, before the mock GitLab replaced the label commands. Scenario B was re-run by hand against the mock. The scenarios marked "(unverified)" were not re-run since then. If you test on a day outside the Saturday–Wednesday working week, bookings move to the next working day.

### A. Webhook ingress (RMO-1, 2, 3, 20) (unverified)

These steps send hand-made webhooks with `post`. Do not run the worker after them.

| Step | Command | Expected |
|---|---|---|
| Wrong token | `post u-x '{}' wrong` | `HTTP 401`; nothing in *Inbox events* |
| Valid open | `open_mr 100` | `HTTP 202`; one inbox event, `processed_at` empty |
| Same UUID again | `open_mr 100` | `HTTP 202`; still one inbox event |
| Malformed MR hook | `post u-bad '{"project":{"id":7}}'` | `HTTP 400`; nothing stored |
| Not JSON | `post u-bad2 'hello'` | `HTTP 400` |

### B. Happy path: open, AI pass, two areas, booking (RMO-4, 9, 12, 14, 17, 19; GLI-5, 6, 12)

1. `gmock open 7 11 --reviewer 90 --file api/orders.py --file web/orders.tsx`, then run the worker. MR 1 is `awaiting_ai_review`.
2. `gmock approve 7 1 90`, then run the worker.
3. Expected:
   - MR 1 is `in_review`, with two area reviews (strict policy, one per area).
   - The `api` review went to **Sara** and the `web` review to **Reza**. Each reason reads like `primary owner; 2026-10-26; 120 of 120 minutes free`.
   - `gmock state` lists reviewers 90 (the AI Reviewer), 21 and 22 on MR 1, and the worker prints one chat message to each human reviewer.
   - *Outbox calls*: `resolve_areas`, 2× `set_reviewer` and 2× `notify`, all `done`.
   - *Audit events*: `mr.opened`, `mr.ai_review_verdict`, two `area_review.created`, two bookings, and `mr.in_review`.
   - `GET /api/merge-requests/1/` shows the same data. Logged out, it returns 403.

### C. AI fail (GLI-6) (unverified)

`gmock open 7 11 --reviewer 90 --file api/a.py`, then `gmock request-changes 7 2 90`, then run the worker. Expected: MR 2 is `waiting_on_author`, with no area reviews and no booking.

### D. Unknown path, so triage (RMO-11) (unverified)

`gmock open 7 11 --reviewer 90 --file docs/readme.md`, then `gmock approve 7 3 90`. Run the worker.

Expected: MR 3 is `needs_triage`, and the chat output shows `([99], 'MR 7:3 needs triage: no code area for docs/readme.md.')`.

If you open an MR with no `--file`, it goes to triage too, with the message "no changed files".

### E. Oversized MR (RMO-8) (unverified)

`gmock open 7 11 --reviewer 90 --too-large api/big.py`, then `gmock approve 7 4 90`. Run the worker. The mock marks the diff of `api/big.py` as too large (GLI-2).

Expected: a size warning to the author, `([11], 'MR 7:4 has ... changed lines, over the size limit ...')`. The MR is still booked.

### F. Unknown author, so quarantine (RMO-21) (unverified)

`gmock open 7 777 --reviewer 90 --file api/a.py`, then run the worker. Person 777 does not exist.

Expected:
- The worker logs `event … failed` with a traceback, and it **keeps running**.
- The inbox event has `failed_at` and `last_error` set. The admin gets a chat alert.
- Any later event for MR 5 stays pending.
- **Recover:** create a Person with GitLab id 777, then in the admin clear the event's `failed_at`. Save, and run the worker again. MR 5 is created.

### G. Only owner is the author, so unbooked (RMO-12, 15) (unverified)

`gmock open 7 11 --reviewer 90 --file infra/deploy.yaml`, then `gmock approve 7 6 90`. Run the worker.

Expected:
- The author is never booked, so the area review stays `unbooked` and MR 6 stays `awaiting_booking`.
- Chat shows `([99], 'MR 7:6: no reviewer fits within 10 days for area review …')`.

### H. Capacity and ranking (RMO-12, 13) (unverified)

The slot is 60 minutes and the daily budget is 120 minutes, so a reviewer takes at most two slots a day.

Open three more `api` MRs with `gmock open 7 11 --reviewer 90 --file api/x.py`, approve each one with `gmock approve`, and run the worker after each one.

Expected: bookings fill the earliest day first.
- Sara takes slots while she has free budget that day, because primary ranks before backup.
- When Sara is full, the next `api` review goes to Reza (the backup) on the same day, if he has budget.
- When neither has budget, the review moves to the next working day.
- The reason on each booking shows the free minutes that decided it.

To change the numbers, edit `RMO_SLOT_MINUTES` and `RMO_DEFAULT_DAILY_BUDGET_MINUTES` in `orchestrator/settings.py`, or set *Daily budget minutes* on a person in the admin.

### I. Leave (RMO-12) (unverified)

In the admin, add a *Leave* for Reza covering today and tomorrow. Then open and approve a `web` MR (`--file web/c.tsx`).

Expected: it is booked to Reza on the first working day after his leave, here 2026-10-28.

### J. Combined coverage (RMO-10) (unverified)

Set `RMO_COVERAGE_POLICY = "combined"` in `orchestrator/settings.py`, then restart both the server and the shell.

Open and approve an MR with `--file api/a.py --file web/b.tsx`.

Expected: Reza owns both `api` and `web` and is not the author, so the MR gets **one** area review that covers both areas. If nobody except the author owns all the touched areas, it falls back to one review per area.

### K. Audit log cannot be changed (RMO-18)

In the admin, open any *Audit event*. It has no Save or Delete button.

In the shell:

```python
from audit.models import AuditEvent
AuditEvent.objects.update(action="x")
```

This raises `AuditImmutableError`.

### L. Developer panel (DVP)

1. In the admin, create a Django user. Open a *Person* and set its *User* field to that user.
2. Open `http://localhost:8000/panel/login/` and sign in with that user. The admin stays signed out until you sign in there too. A user with no Person gets a 403 page.
3. Check the three tabs:
   - *My MRs*: only MRs the Person authored. Open one to see area reviews, the next step, and the history.
   - *My reviews*: bookings from today to the booking horizon, grouped by day with booked minutes over budget.
   - *Availability*: tick working days and pick a budget (60, 120, 180 or 240); each change saves by itself and shows "Saved" next to the heading (unticking every day shows "Select at least one working day."). Add or remove leave with its buttons. With scripts off, the Save buttons come back. After each save, open *Audit events* in the admin. There is one event with your user, the action, and the before and after values.
4. Change the id in `/panel/mrs/<id>/` to an MR of another author. It must return 404.

### M. Go to MR and author presence (MRV)

1. Start the mock with `--public-url` set to the address the mock has for you (default `http://127.0.0.1:8080`). Open an MR as in scenario B and let the worker run. In the admin, the *Merge request* has a `web_url` such as `http://127.0.0.1:8080/group/project-7/-/merge_requests/1`.
2. Sign in to the panel as the booked reviewer. On *My reviews* the booking shows a "Go to MR" link. It opens the address in a new tab. A booking whose MR has no `web_url` shows no link.
3. Type a note and press "Request author". The badge "Author requested" and a "Cancel request" button replace the request button. Press "Request author" again in a second tab: nothing new is stored. *Outbox calls* has one `notify` call for the author, and *Audit events* has `presence.requested`.
4. Sign in as the author. Open the MR in *My MRs*. "Presence requests" lists the reviewer, area, day and note.
5. Press "Cancel request". The author gets a second `notify` call, and the request disappears from the MR page.
6. Post to `/panel/reviews/<id>/presence/` for a booking of another reviewer, or with a note over 500 characters. The first returns 404, the second 400, and nothing is stored.

### N. Outcomes, approval and follow-up (RVO)

1. Open an MR as in scenario B, so MR 1 is `in_review` with a booked reviewer per area (Sara on `api`, Reza on `web`).
2. Reviewer approvals: `gmock approve 7 1 21`, then run the worker. The `api` area review becomes `approved`; MR 1 stays `in_review`. Then `gmock approve 7 1 22` and run the worker: MR 1 is `approved`. *Audit events* has two `area_review.approved` and one `mr.approved`. The panel shows the badge "Approved".
3. Changes requested: open MR 2 the same way. `gmock request-changes 7 2 21`, then run the worker. MR 2 is `waiting_on_author`, the reviewers stay, and the author gets a `notify` call "Sara requested changes on !2.". An approval or a change request from before the last push is ignored.
4. Follow-up: push to MR 2 and have the AI Reviewer approve again, then run the worker. No area review is created. The `api` area review gets a new pending session, marked "Follow-up", with Sara. A session that was still pending shows the outcome `superseded` and its minutes are free again. An area that GitLab still shows as approved gets no new session.
5. Done, Need more time and Hand back (scenarios M and the panel): sign in as the booked reviewer and press each button on *My reviews*. Done changes no approval. Need more time books Sara again on the next day that fits. Hand back books another owner of the area.

### O. Merge routing (MRG)

1. In the admin, open *Merge routing settings*. Set the default daily merge limit (5) and the merge window length (30 minutes). A window length of 0 and a time such as `25:00` in a Person are rejected. Make Dana (id 31) a merge owner: tick *Is merge owner*, set *Merge times* to `10:00, 15:00`, and leave the daily limit empty.
2. Bring MR 1 to `approved` as in scenario N. *Merge assignments* has one open row for Dana, with a window and a reason, and Dana has a `notify` call "Merge !1 ... in your 10:00 window on ...". Without a merge owner, the admin gets the alert "no merge owner fits". Dana sees the MR on *My merges*.
3. Merge by the right person: `gmock merge 7 1 31`, then run the worker. MR 1 is `merged`. The row has the merger and the time, and *Audit events* has `mr.merged`. A merge outside the window shows `outside_window` in the event; a merge by someone else shows `by_other_person`. Neither sends an alert.
4. Merge by a violator: repeat with MR 2 and `gmock merge 7 2 21` (Sara, an approver) or the author. The row has `violation` set and the admin gets "Merge violation on MR 7:2 ...: merged by Sara. Role: approver.".
5. Merge before approval: `gmock merge 7 3 31` on an MR that is still `in_review`. The row has no merge owner and the violation `before_approval`, and the admin gets an alert.
6. Lost webhook: `gmock webhooks off`, merge an approved MR, run the reconciler. A `reconcile` inbox event with the action `merged` is stored, and handling it records the merge as above.
7. Revoked approval: with an approved MR, remove an approval in the mock (`gmock request-changes 7 4 21`). After the worker runs, MR 4 is back in `in_review` (or `waiting_on_author`), the assignment has `cancelled_at`, and Dana gets "Do not merge !4 ...". The next approval assigns it again.
8. Daily retry (MRDR): with no merge owner free, bring MR 5 to `approved`; the admin gets "no merge owner fits". In *Merge routing settings*, set *Daily retry time* to a minute from now (`Asia/Tehran`). A time such as `25:00` is rejected. When the worker passes that time, *Audit events* has `merge.retry_run` with the trigger `daily`, and the admin gets one alert "1 approved MR(s) still have no merge owner: 7:5 ... — waiting 0 day(s)". Later passes the same day do nothing.
9. Manual retry: make Dana a merge owner again, open *Merge requests*, select MR 5 and one MR that is not approved, and run the action *Retry merge routing*. The page shows "1 assigned, 0 still unassigned, 1 skipped.", MR 5 has an open assignment, and Dana gets the chat message.

### P. Daily rebooking (RBW) (unverified)

1. Start the mock and the worker as in scenario B. Make MR 1 touch an area whose only owner (Sara, id 21) is on leave or has a budget of 0, so it is `unbooked`. The admin gets the first "no reviewer fits" alert, and MR 1 stays `awaiting_booking`.
2. Give Sara capacity again in the admin (*Availability*, or raise her budget). Nothing is booked yet.
3. In *Rebooking and watchdog settings*, set *Daily run time* to a minute in the past (`Asia/Tehran`), on a day when the worker has not yet rebooked. Run the worker once (`run_worker --once`, as in §3). MR 1 is `in_review`, the area review is `booked`, and *Audit events* has `area_review.booked`, `mr.in_review` and one `booking.retry_run` with the trigger `daily` and booked 1. Sara has a `notify` call. A second worker pass the same day does nothing.
4. With another MR with no free owner, the run sends no per-review alert. The admin gets one "1 area review(s) still have no reviewer:" summary with the days waited.
5. Retry booking: add an owner, open *Merge requests*, select the MR and one MR that is not unbooked, and run the action *Retry booking*. The page shows "1 booked, 0 still unbooked, 1 skipped." and the audit event has the trigger `admin`.

### Q. Stale-MR watchdog (RBW) (unverified)

1. In *Rebooking and watchdog settings*, set the AI review, triage and stale thresholds to 1.
2. No AI verdict: open an MR in the mock and stop the AI Reviewer from answering. Move its *Ai gate entered at* back by three days in the shell (`MergeRequest.objects.filter(iid=1).update(ai_gate_entered_at=...)`).
3. Missed slot: book an MR, then move the session's day to a past working day. Needs triage: do the same with an MR in `needs_triage` and an earlier *State changed at*.
4. Run the worker after the rebooking has run that day. *Audit events* has one `watchdog.run` with a count per case. The admin gets one alert "Stuck MRs (N):" grouped by case, and the reviewer of the missed slot gets "Your review of MR ... has no outcome.". A second pass the same day does nothing.
5. The next day, the same MRs are reported again. A `waiting_on_author` MR, an unbooked area review and an approved MR without a merge owner are never listed.

---

## 5. Reset

Delete all manual data and start over. This does not touch the test database:

```bash
docker exec rmo-test-pg psql -U orchestrator -c "DROP DATABASE IF EXISTS orchestrator WITH (FORCE);" postgres
docker exec rmo-test-pg psql -U orchestrator -c "CREATE DATABASE orchestrator;" postgres
.venv/bin/python manage.py migrate && .venv/bin/python manage.py createsuperuser
```

To stop Postgres: `docker rm -f rmo-test-pg`. `scripts/test.sh` starts it again.

## 6. Smoke test against the work GitLab

The orchestrator was built against `gitlab_mock`, which follows the GitLab docs. Run this checklist once against the real instance before you trust the integration. Start with a read-only token (scope `read_api`) and a project you can read. Booking (`set_reviewer`) needs a write token, so test it last, on a throw-away MR.

Settings (environment):

- `RMO_GITLAB_GATEWAY=integrations.gitlab.GitLabRestGateway`
- `GITLAB_URL` (https), `GITLAB_TOKEN`, `GITLAB_WEBHOOK_TOKEN`
- `RMO_AI_REVIEWER_GITLAB_USER_ID`: the id of the AI Reviewer user
- `RMO_GITLAB_PROJECT_IDS`: comma-separated project ids for reconciliation, for example `12,34`
- `RMO_RECONCILE_INTERVAL_SECONDS` (default 300)

Checks. Mark each one on the real instance; a "no" is a finding to report.

1. **First pass, read-only and without side effects.** Set a read-only token and one project id. Do not run `run_worker` yet: the worker would handle the events and queue real `set_reviewer` calls. In `manage.py shell` run only `from reviews.reconciler import reconcile_all; reconcile_all()`. Then `ReconcileCursor.objects.values()` has a row for the project, and in the admin *Inbox events* has one `reconcile` event per open MR. No request failed with 401 or 403.
2. **Reviewer state names.** `GET /projects/:id/merge_requests/:iid/reviewers` on an MR the AI Reviewer reviewed. The `state` values are `unreviewed`, `reviewed`, `requested_changes`, `approved` and `unapproved`. If a name differs, update `PASS_STATES` and `FAIL_STATES` in `reviews/ai_gate.py`.
3. **Reviewer-authored system notes.** `GET /projects/:id/merge_requests/:iid/notes?sort=desc&order_by=created_at`. When the AI Reviewer approves, a note with `system: true` is authored by it, and its body starts with `approved this merge request` (`APPROVAL_NOTE_TEXT`). When it requests changes, the body starts with `requested changes` (`CHANGES_NOTE_TEXT`). Both constants are in `integrations/gitlab.py`. If GitLab writes no such note, or other text, the fresh-verdict rule (GLI-6) never passes: report it.
4. **Comment-only review.** When the AI Reviewer reviews without approving, it posts a plain comment (`system: false`), and its reviewer state is `reviewed`. If it posts no comment, the MR waits in `awaiting_ai_review` without notice.
5. **"Remove approvals on push".** Read the project's setting (*Settings > Merge requests > Approvals*). When it is on, a push removes the AI Reviewer's approval; the bot must approve again. The orchestrator expects that: it treats the approval as fresh only when it is newer than the push.
6. **MR diff overflow.** Open an MR with more than 1000 changed files, or a very large diff. Check `changes_count` in the MR JSON (a string such as `"1000+"`) and that `GET .../diffs` returns every file on all pages, or marks files `too_large` or `collapsed`. If the diffs stop early without a marker, the MR can escape its area: report it.
7. **Reviewer PUT keeps the AI bot.** On a throw-away MR with the AI Reviewer as reviewer, let the orchestrator book a reviewer (or send `PUT /projects/:id/merge_requests/:iid` with `reviewer_ids` = the current ids plus one). The MR still lists the AI Reviewer and any other current reviewer.
8. **Lost webhooks.** Stop the webhook, push a commit to an MR the AI Reviewer already approved, let the bot approve the new commit, and run the worker. The MR moves back through the AI gate and on to `awaiting_booking` or `in_review`.
9. **Diff versions.** `GET /projects/:id/merge_requests/:iid/versions`. Each entry has `head_commit_sha` and `created_at`; the entry for the MR's current `sha` is the time of the last push, and an approval or a comment does not add one. If an approval adds a version, the entry time of the AI gate is wrong.
10. **`sha` fields.** The MR list (`GET /projects/:id/merge_requests?state=opened`) has `sha` on every MR, and a Merge Request Hook has `object_attributes.last_commit.id`. A null `sha` (empty MR) is allowed.
11. **`updated_at` moves.** A bot approval, a bot comment and a push each move the MR's `updated_at`. If an approval does not, reconciliation never reads it.
12. **List filters.** `updated_after` is inclusive (the MR at that exact time is listed again; the orchestrator relies on its dedup), and `order_by=updated_at&sort=asc` is honoured.
13. **Note Hook.** A bot comment on an MR sends a Note Hook to the orchestrator (the webhook must include *Comments*).
14. **MR web address.** A Merge Request Hook carries `object_attributes.url`, and the MR list carries `web_url`. Both are absolute `https` URLs of the MR page. If the hook field has another name, no link shows on *My reviews* until the next reconciliation.
15. **Chat text is plain.** The real chat adapter must send notification text as plain text, with no markdown, HTML or mention parsing. Presence request notes are typed by developers and go into the text as they are.

16. **Merge hook fields.** Merge an MR in the throw-away project. The Merge Request Hook has `object_attributes.action` = `merge`. The orchestrator does not read the merger from the hook: it queues a `confirm_merge` job, which reads the MR from GitLab.
17. **`merge_user` and `merged_by`.** `GET /projects/:id/merge_requests/:iid` on that MR has `state` = `merged`, `merged_at`, and `merge_user.id` (the person who merged; the orchestrator reads it first) or `merged_by.id`. Check both on a normal merge and on a "merge when pipeline succeeds" merge. When neither is set, the orchestrator records an unknown merger and alerts the admins that separation of duties was not checked.

With a write token and a throw-away MR, `run_worker --once` can need a second run, because it skips events younger than `RMO_INBOX_CLAIM_GRACE_SECONDS` (2 s). Run it again after a few seconds.
