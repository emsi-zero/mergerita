# rmo workflow — how changes are made in merge-orchestrator

This is the human source of the project's engineering workflow. Skills, agents
and hooks follow it. When a skill disagrees with this file, this file wins.
`config.json` is its machine half: the check commands and the risk paths.

It comes from the craft-workflow plugin and keeps its core contract. A spec
is approved before code. Every criterion gets exactly one test. Gate A is the
tests and tools. Gate B is the review. Nothing is `done` without a recorded
command. It is tuned to this repository: a Django + Postgres prototype that
drives GitLab merge requests through review booking and merge routing. Its
hard parts are locks, the inbox/outbox, GitLab freshness and panel scoping,
not money.

```
.claude/rmo/
├── WORKFLOW.md          this file
├── INVARIANTS.md        LOCK / TX / IO / GL / SEP / AUTH / DATA rules, cited by ID
├── reviewer-memory.md   accepted trade-offs and suppressions
├── config.json          check commands, test dirs, risk paths
├── bin/rmo              the CLI (spec, approve, check, gate-a, trace, risk, hooks)
├── templates/           spec.md, design.md, tasks.md, log.md
├── lenses/              spec, style, logic, security, query
└── state/               approvals.json (committed), session.json, logs/ (ignored)
.claude/skills/rmo*      router, routes, spec, plan, review, conventions, debug, finish
.claude/agents/rmo-*     coder, reviewer, deep-reviewer
.claude/specs/           one directory per change (same layout as before)
```

`rmo` below means `.claude/rmo/bin/rmo`, run from the repository root.

---

## 1. Routes

| Route | Use it for | Spec | Implementer | Review |
|---|---|---|---|---|
| **quick** | Typo, copy text, template markup, a docs-only change, a rename, a config default, a one-line fix with a known cause | None | Main session | None. Focused checks plus a self-read against the `style` lens |
| **task** | One MR: one rule, one admin action, one panel view, one bug fix that needs a regression test | `spec.md` + `tasks.md` | Main session | One `rmo-reviewer` pass, plus `rmo-deep-reviewer` on `Risk: high` |
| **project** | A new lifecycle stage or MR state, a new GitLab read or hook, a new outbox job family, work across more than two apps, or more than one session of work | + `design.md`, sessions | `rmo-coder` per session | The same, once per session |

**Forces project:** a new MR or area-review state or transition, a new external
GitLab call or webhook kind, a new auth path, a data migration, or a change to
the lock order (LOCK-1).

**Forces at least task:** any write path (a service function that saves), a new
outbox handler, a new admin action, a new panel URL, a schema migration, a new
setting or env var, or any behaviour change that an existing test pins.

**Quick may never touch:** a lock, a transaction boundary, the inbox, the
outbox, the GitLab gateway, the webhook view, panel auth or scoping, or a
migration. Promote to task and say why in one line.

## 2. Risk tier

Each spec states `Risk: low` or `Risk: high`. Under the craft plugin every
project here came out `high`. This rule is narrower, so the Opus review runs
where it pays off.

**High** when the change adds or alters any of these:
- a lock, a lock order, a `select_for_update`, an advisory lock, or a partial
  unique constraint (LOCK-*)
- a transaction boundary, or what is committed with the audit or outbox (TX-*)
- inbox claiming, outbox leasing, retry or quarantine, or a batch job (IO-*)
- a GitLab read that decides state: freshness, merger, verdicts, reconcile (GL-*)
- webhook auth, panel authentication, session handling, or panel scoping (AUTH-*, GL-5)
- a data migration, or a constraint added to a populated table

**Low** otherwise: read-only views and templates scoped through the existing
mixin, admin display, copy, docs, additive fields with `db_default`, and pure
functions with no I/O.

`rmo risk` lists the changed paths that hit `config.json` risk paths. It is a
prompt to check, not a verdict. The spec states the reason for its tier in one
line under the header.

## 3. Review routing

| Reviewer | Model | Runs | Lenses |
|---|---|---|---|
| `rmo-reviewer` | sonnet | every task and project review | the routed lenses. On `Risk: high` it skips `logic` and `security` |
| `rmo-deep-reviewer` | opus | `Risk: high` only, in parallel | `logic` + `security`, limited to the invariant families the spec lists under "Holds", plus the spec's own INV rows |

| Lens | Runs when the diff touches |
|---|---|
| `spec` | always (task and project) |
| `style` | always |
| `logic` | any `reviews/`, `integrations/` or `audit/` service code, tests, migrations, worker, handlers |
| `security` | views, `panel/`, `orchestrator/settings.py` or `urls.py`, admin actions, the webhook, the gateway, templates that render GitLab data |
| `query` | models, querysets in services or views, admin `list_display`, migrations |

**Re-review is a delta.** Only the reviewer that raised a confirmed Critical or
High reviews again, and only the diff since its review. Medium and Low fixes
need Gate A, not a re-review.

## 4. Specs and gates

Specs live in `.claude/specs/<date>-<slug>/`, and `.claude/specs/ACTIVE` names
the active one. `rmo new` creates it. `rmo approve` binds the developer's
approval to the SHA-256 of `spec.md`, and it refuses while template comments
or placeholders remain. Any later edit to `spec.md` shuts the gate again until
the delta is approved. State goes in `tasks.md` and `log.md`, never in `spec.md`.

- **Gate A — spec verified:** `rmo gate-a` is green. That means
  `scripts/test.sh -q`, pylint 10.00, isort, black, `makemigrations --check`,
  and `rmo trace`: every criterion has exactly one test that names its ID.
  `--log "Session N Gate A"` writes the result line into `log.md`.
- **Gate B — review green:** every routed lens ran, no confirmed Critical or
  High is open, and every checklist item is answered. The adjudicated round goes
  into `log.md` with `rmo log`.

States: `pending`, `in progress`, `tests green`, `in review`, `blocked`,
`done`, `abandoned`. Writing the code is not done. Green tests are not done.

**Hooks** (in `.claude/settings.json`; `RMO_HOOKS=off` disables them for one
command):
- `SessionStart` prints the active spec, its gate, its tasks and its last checks.
- `PreToolUse` (Write/Edit) on the task and project routes denies any write
  outside `.claude/` until the spec is approved. It always denies hand edits
  to `.claude/rmo/state/`.
- `Stop` refuses a message that claims `done` or a green gate when the last
  recorded run of a Gate A check failed or never ran.

## 5. Checks

| Check | `rmo check <name>` runs |
|---|---|
| tests | `scripts/test.sh -q [args]`: Postgres 14 on a unix socket in `.pgsock/`. Focused: `rmo check tests -- reviews/tests/test_merge_router.py` |
| lint | `env DEBUG=1 .venv/bin/pylint <all apps>`. Focused: `rmo check lint -- reviews/merge_router.py` |
| imports | `.venv/bin/isort --check --diff .` |
| format | `.venv/bin/black --check .` |
| migrations | `env DEBUG=1 .venv/bin/python manage.py makemigrations --check --dry-run` |

In a git worktree that has no `.venv`, `rmo` uses the main checkout's `.venv`.
`scripts/test.sh` still needs `.venv` at the repository root, so symlink it
there: `ln -s <main checkout>/.venv .venv`. Ad-hoc commands go through
`rmo check run -- <cmd>`, so their exit code is recorded.

## 6. Skills

| When | Skill |
|---|---|
| Any coding request, or resuming work | `rmo`, the router |
| A route chosen explicitly | `rmo-quick`, `rmo-task`, `rmo-project` |
| Writing a spec or a spec delta | `rmo-spec` |
| Design and sessions for an approved project spec | `rmo-plan` |
| Writing or changing code or tests | `rmo-conventions` |
| Running or adjudicating a review | `rmo-review` |
| A failing test, a stuck MR, a quarantined event, a FAILED outbox job | `rmo-debug` |
| Docs, the MR description, closing the spec | `rmo-finish` |

## 7. Cost rules

- `task` implements in the main session. `rmo-coder` is only for project sessions.
- Give subagents paths and IDs, not pasted content.
- One reviewer pass per round. Opus only on `Risk: high`, and only on the
  invariant families the spec lists.
- Checks go through `rmo`, which prints the summary or the failures and keeps
  the full log in `.claude/rmo/state/logs/`.
- Resume from the spec and `tasks.md`. The SessionStart hook prints where things stand.
