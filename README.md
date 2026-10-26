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

In admin, enter people, code areas (glob path patterns) and ownership. Then send a GitLab `Merge Request Hook`, first with `action: open`, then with `action: update` and the label `ai-review::passed`. Limit: the fake gateway keeps its seeded changes in memory, and only tests can seed it. So in a manual run, every MR has no changed files and goes to `needs_triage`. The full path through booking is shown by the tests, for example `reviews/tests/test_audit_trail.py`. A manual demo needs a real or file-backed `RMO_GITLAB_GATEWAY`.

Pylint and `manage.py` need `DEBUG=1`. When DEBUG is off, settings require `SECRET_KEY`.
