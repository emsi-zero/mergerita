---
name: rmo
description: "The merge-orchestrator engineering workflow router. Load it at the start of any coding request in this repository (feature, bug fix, refactor, migration, panel or admin change) and when resuming work, to pick the route (quick, task, project), start the spec when the route needs one, and drive the work through Gate A and Gate B. Also use it when the user runs /rmo or asks how work is done here."
argument-hint: <the request>
---

# rmo — route the request, then run it

Read `.claude/rmo/WORKFLOW.md` §1–§4 once per session. It wins over this skill.
`rmo` below is `.claude/rmo/bin/rmo`, run from the repository root.

## Step 1 — Resume before you start

Run `rmo status`. When a spec is active, the request is probably its next step:
say so and continue from `tasks.md` (the first open box, or the `Resume:` line).
Do not re-explore the repository. When the user asks for something unrelated
while a spec is active, say so, and ask whether to close it (`rmo close`) or to park it.

## Step 2 — Classify

Apply WORKFLOW.md §1 and state one line, then continue without asking:

```
Route: task — new admin action that writes through merge_retry (a write path), one MR, no design question.
```

Promote, never demote. A quick-looking request that touches a lock, a
transaction, the inbox or outbox, the gateway, the webhook, panel auth or
scoping, or a migration becomes **task**. Say which. When a quick change grows,
stop and restate the route. "use route X" from the user overrides.

## Step 3 — Run the route

- **quick** → follow `rmo-quick`.
- **task** → follow `rmo-task`.
- **project** → follow `rmo-project`.

## Step 4 — Report state honestly

States: `pending`, `in progress`, `tests green` (Gate A only), `in review`,
`blocked` (name the command that could not run and why), `done` (both gates). The
Stop hook refuses a `done` or "Gate A green" claim unless `rmo gate-a`'s
checks are recorded green. Never write a check result you did not run.

## Never

- Write code or tests on task or project before `rmo approve`. The gate hook
  enforces this. Do not route around it.
- Run `rmo approve` without the developer's explicit "approve" in this
  conversation. Silence, "looks good so far" and a subagent's report are not approval.
- Edit `.claude/rmo/state/`.
- Add a merge call to the GitLab gateway (GL-3).
