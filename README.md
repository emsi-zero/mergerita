# Review & Merge Orchestrator — thin-slice prototype

A prototype of the design in `docs/architecture-design.md`. It covers webhook ingress, the MR lifecycle with the AI-review gate, the area resolver, and explainable booking. All external systems (GitLab, chat, calendar) are in-memory fakes behind adapter interfaces.

Spec, design and the review log: `.claude/specs/2026-10-26-orchestrator-thin-slice-prototype/`.

## Run the tests

```bash
scripts/test.sh -q
```

The script starts a local `postgres:14` container that listens on a unix socket in `.pgsock/`. It runs pytest from `.venv/`. To create the venv on a machine without `python3-venv`:

```bash
python3 -m venv --without-pip .venv
<any python with pip> -m pip --python .venv/bin/python install pip -r requirements.txt
```

`Dockerfile` and `docker-compose.yml` exist, but they have not been built yet. Docker Hub pulls were blocked on the development machine.

## Run it

```bash
export DEBUG=1 GITLAB_WEBHOOK_TOKEN=dev-token DB_HOST=$PWD/.pgsock
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver        # POST /webhooks/gitlab/, GET /api/merge-requests/<id>/, /admin/
.venv/bin/python manage.py run_worker       # inbox, then outbox, in a loop (--once to drain)
```

In admin, enter people, code areas (glob path patterns) and ownership. For a manual demo, run the mock GitLab and point the orchestrator at it:

```bash
export RMO_GITLAB_GATEWAY=integrations.gitlab.GitLabRestGateway
export GITLAB_URL=http://127.0.0.1:8080 GITLAB_TOKEN=mock-token
export RMO_AI_REVIEWER_GITLAB_USER_ID=90      # GitLab user id of the AI Reviewer bot
.venv/bin/python scripts/gitlab-mock serve --port 8080 --token mock-token \
  --webhook-url http://127.0.0.1:8000/webhooks/gitlab/ --webhook-secret dev-token
.venv/bin/python scripts/gitlab-mock open 7 11 --reviewer 90 --file api/a.py   # author 11 opens an MR
.venv/bin/python scripts/gitlab-mock approve 7 1 90                            # the AI Reviewer approves
```

Run the worker. The MR moves to booking and the mock lists the booked reviewers. `docs/testing.md` has the full demo.

Pylint and `manage.py` need `DEBUG=1`. When DEBUG is off, settings require `SECRET_KEY`.

## Developer panel

Developers sign in at `/accounts/login/` and use `/panel/`. An admin links each Django user to a Person in the admin (Person → User). The password login has no attempt limit, so run the panel only inside the company network until GitLab SSO replaces it.

The panel has three tabs: My MRs, My reviews and Availability. In Availability a developer sets working days, a daily budget (60, 120, 180 or 240 minutes) and leave. Each change writes one audit event. See `docs/testing.md` (scenario L) for how to check it.
