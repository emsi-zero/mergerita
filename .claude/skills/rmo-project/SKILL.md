---
name: rmo-project
description: "Project route of the merge-orchestrator workflow: multi-session work — a spec, approval, design.md with write paths and locks, sessions in tasks.md, rmo-coder per session, Gate A and review per session. Use it when the user runs /rmo-project or the rmo router picks project (a new MR state, a new GitLab read or hook, a new outbox job family, a data migration, cross-app work)."
argument-hint: <the project>
---

# rmo-project

`rmo` = `.claude/rmo/bin/rmo`.

1. **Spec.** Follow `rmo-spec` with `--route project`. Stop for approval, then `rmo approve`.
2. **Plan.** Follow `rmo-plan`: write `design.md` and the sessions in `tasks.md`.
   Present the session table and stop for a go-ahead. A plan is cheaper to fix than code.
3. **Per session N:**
   - Spawn `rmo-coder` with the spec directory and N. Pass paths, not pasted content.
     For sessions that edit the same files as an unfinished session, wait.
   - When it reports, run Gate A yourself: `rmo gate-a --session N --log "Session N Gate A"`.
     Do not trust the coder's report on its own.
   - Gate B: follow `rmo-review` for the session's diff (`git diff` against the session's
     starting commit, or the working tree).
   - Fix the confirmed findings, or delegate the fixes back to `rmo-coder` with the
     finding list. Re-review only the delta after a confirmed Critical or High.
   - Set the session `State: done` in `tasks.md` and log it.
4. Do not start session N+1 while session N is `blocked`, unless the developer says so.
5. **Close** with `rmo-finish` when every session is done.
