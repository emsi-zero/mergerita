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
2. Open `http://localhost:8000/accounts/login/` and sign in with that user. A user with no Person gets a 403 page.
3. Check the three tabs:
   - *My MRs*: only MRs the Person authored. Open one to see area reviews, the next step, and the history.
   - *My reviews*: bookings from today to the booking horizon, grouped by day with booked minutes over budget.
   - *Availability*: save working days (at least one), a budget (60, 120, 180 or 240), and add or remove leave. After each save, open *Audit events* in the admin. There is one event with your user, the action, and the before and after values.
4. Change the id in `/panel/mrs/<id>/` to an MR of another author. It must return 404.

---

## 5. Reset

Delete all manual data and start over. This does not touch the test database:

```bash
docker exec rmo-test-pg psql -U orchestrator -c "DROP DATABASE IF EXISTS orchestrator WITH (FORCE);" postgres
docker exec rmo-test-pg psql -U orchestrator -c "CREATE DATABASE orchestrator;" postgres
.venv/bin/python manage.py migrate && .venv/bin/python manage.py createsuperuser
```

To stop Postgres: `docker rm -f rmo-test-pg`. `scripts/test.sh` starts it again.
