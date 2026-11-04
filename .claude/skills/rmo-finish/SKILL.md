---
name: rmo-finish
description: "Close a merge-orchestrator change after both gates are green: verify docs (docs/testing.md scenario, README, smoke items, INVARIANTS.md), diff hygiene, MR description traced to criteria, close the spec. Use it when the user runs /rmo-finish or the task or project route ends."
---

# rmo-finish

`rmo` = `.claude/rmo/bin/rmo`. It never commits or pushes unless the developer asks.

1. **Gates.** `rmo status` must show every Gate A check PASS and the spec
   `[approved]`, and `log.md` must have a green Gate B. Otherwise the state is not
   done: say what is missing and stop.
2. **Docs** (the spec's `Docs` section):
   - `docs/testing.md` §4 has the scenario (the next letter), with its steps through
     `scripts/gitlab-mock`. Mark it "(unverified)" unless someone ran it by hand.
   - §6 has a smoke-test item for each new GitLab field or assumption.
   - The README covers what admins or developers now see or set up.
   - `.claude/rmo/INVARIANTS.md`: add any new rule that later changes must keep,
     with its source, and bump the "next scenario letter".
   - `.claude/rmo/reviewer-memory.md`: add the trade-offs the developer accepted.
3. **Hygiene.** In `git status` and `git diff --stat`, look for unrelated edits,
   debug prints, stray files, a migration without its Contracts entry, or a
   leftover `.only` or `skip`. Run `rmo gate-a` again when anything changed after
   the last green run.
4. **MR description**, short and in plain English:
   - Title: what changes for whom.
   - Why: the spec's problem, in 2 sentences.
   - What: one bullet per criterion ID with its test.
   - Contracts: migrations (app + number; say if the worker must stop), settings,
     admin actions, URLs, and texts.
   - How to try it: the `docs/testing.md` scenario letter.
   - Accepted trade-offs and follow-ups.
5. **Close.** Set `tasks.md` to `State: done`, run `rmo log "done" "<one-line summary>"`,
   then `rmo close`. Commit only when the developer asks. This repository's
   commit subjects are imperative sentences ("Merge routing daily retry and
   manual retry in the admin"), and a separate commit closes the spec ("Close the … spec").
