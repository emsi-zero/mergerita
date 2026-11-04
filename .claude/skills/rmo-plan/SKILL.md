---
name: rmo-plan
description: "Turn an approved merge-orchestrator project spec into design.md (shape, write paths and locks, migrations, failure modes) and session blocks in tasks.md. Use it after rmo approve on the project route, or when the user runs /rmo-plan. It plans only; it writes no code."
---

# rmo-plan

Precondition: `.claude/rmo/bin/rmo status` shows the spec `[approved]`.
Otherwise stop.

## design.md

1. **Shape** — which `reviews/` module owns each piece. Reuse an existing module
   when its name already covers the job: lifecycle, ai_gate, area_resolver,
   booking, coverage, outcomes, presence, availability, merge_router, merges,
   merge_confirmation, merge_retry, reconciler. Add a module only for a new
   concept. Views, handlers and admin actions stay thin.
2. **Write paths and locks** — one row per command that writes. List its locks in
   LOCK-1 order, the GitLab read it does *before* the transaction, its audit
   action, and its outbox jobs. A row that needs a GitLab call inside the lock is
   a design error: split it into "read, then lock and write" (TX-2).
3. **Data and migrations** — fields with `db_default`, partial unique constraints
   for "one open X", and for each data migration, whether the worker stops.
4. **Failure modes** — for each new job, handler or batch: a GitLab error, a
   malformed payload, a crash mid-run, a second concurrent run (IO-2, IO-3).
5. Add no behaviour the spec does not state. When the design needs some, propose
   a spec delta.

## tasks.md sessions

- A session is one sitting: one coherent diff that passes Gate A on its own and is
  reviewable in one pass. Usually three to six tasks.
- Order the sessions: models and migration → service and write path → handlers,
  worker or admin wiring → panel and templates → docs. Merge two adjacent kinds
  when they are small.
- Each block: `## Session N — <name>`, `State: pending`, `Covers: <IDs>`
  (every criterion is covered exactly once across the sessions), then the tasks with
  their file and ID, the Gate A line, the Gate B line, and `Resume:`.
- The concurrency tests (LOCK-6) belong in the session that adds the lock.
- The last session holds the docs: the `docs/testing.md` scenario, README, smoke items.

Present the table (session · covers · files · risk notes) and stop for a go-ahead.
