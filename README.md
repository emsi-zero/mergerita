# Review & Merge Orchestrator — thin-slice prototype

A prototype of the design in `docs/architecture-design.md`. It covers webhook ingress, the MR lifecycle with the AI-review gate, the area resolver, and explainable booking. An MR ends at `approved`, once GitLab shows a fresh approval from the booked reviewer of every area (no merge routing yet). All external systems (GitLab, chat, calendar) are in-memory fakes behind adapter interfaces.

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

## Run it with Docker Compose

One command starts PostgreSQL, the migrations, the web app (http://127.0.0.1:8000), the worker, and the mock GitLab (http://127.0.0.1:8080). The web app and worker use the real GitLab client, pointed at the mock. The AI Reviewer is mock user 90, and project 7 is reconciled.

```bash
docker compose up --build                      # Ctrl-C to stop; add -d to run in the background
docker compose run --rm web python manage.py createsuperuser
docker compose run --rm tests                  # the test suite, against the compose database
docker compose down -v                         # stop and delete the database volume
```

Drive the mock from inside its container: `docker compose exec gitlab-mock python scripts/gitlab-mock open 7 11 --reviewer 90 --file api/a.py`, then `… approve 7 1 90`. People, code areas and ownership are added in the admin, or with the seed snippet in `docs/testing.md` §3, piped in with `docker compose exec -T web python manage.py shell`.

Two environment variables cover machines where Docker cannot work out of the box:
- `PYTHON_IMAGE` and `POSTGRES_IMAGE` take the base images from a registry mirror, for example `PYTHON_IMAGE=registry.example.com/library/python:3.12-slim`, when Docker Hub is blocked.
- `COMPOSE_SUBNET` (default `10.252.10.0/24`) changes the stack's network if that range clashes with your VPN.
- `WEB_PORT` and `MOCK_PORT` (defaults 8000 and 8080) change the published ports, for example `WEB_PORT=8010 MOCK_PORT=8090 docker compose up -d` when a host demo already uses them.

**Behind a VPN that captures private ranges** (for example Cisco AnyConnect, with `ip route` showing `10.0.0.0/8 dev cscotun0`), containers on Docker networks are unreachable and nothing loads. Use the host-network override instead; the services then listen on 127.0.0.1, with Postgres on port 55432 (`DB_PORT`):

```bash
cd ~/side-projects/merge-orchestrator
export COMPOSE_FILE=docker-compose.yml:docker-compose.host.yml WEB_PORT=8010 MOCK_PORT=8090
docker compose up -d
docker compose run --rm web python manage.py createsuperuser
# then open http://127.0.0.1:8010/admin/ ; drive the mock with
docker compose exec gitlab-mock python scripts/gitlab-mock --url http://127.0.0.1:8090 open 7 11 --reviewer 90 --file api/a.py
```

Run every `docker compose` command from the project folder. Elsewhere it stops with "no configuration file provided".

The image is built on the host network, because containers on the default bridge may have no DNS, and pip must reach PyPI. Only the build needs the internet.

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

Developers sign in at `/panel/login/` and use `/panel/`. The panel and the admin sign in separately: the panel keeps its own session cookie (`panel_sessionid`), so a panel sign-in does not open the admin, and the reverse. An admin links each Django user to a Person in the admin (Person → User). The password login has no attempt limit, so run the panel only inside the company network until GitLab SSO replaces it.

`PANEL_AUTH_MODES` is a comma-separated list of `local` (username and password) and `sso` (GitLab); the default is `local,sso`. An empty or unknown value stops the application at start. The GitLab sign-in is a stub for now: its pages reply "GitLab sign-in is not set up yet" and sign no one in. With `sso` alone, nobody can sign in to the panel.

The panel has three tabs: My MRs, My reviews and Availability. In Availability a developer sets working days, a daily budget (60, 120, 180 or 240 minutes) and leave. Each change writes one audit event. See `docs/testing.md` (scenario L) for how to check it.
