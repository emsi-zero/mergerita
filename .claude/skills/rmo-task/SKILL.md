---
name: rmo-task
description: "Task route of the merge-orchestrator workflow: one merge request — spec, developer approval, inline implementation, Gate A via rmo gate-a, one review pass, log, finish. Use it when the user runs /rmo-task or the rmo router picks task."
argument-hint: <the task>
---

# rmo-task

`rmo` = `.claude/rmo/bin/rmo`.

1. **Spec.** Follow `rmo-spec`: `rmo new --route task --title "..." [--key KEY]`,
   write `spec.md`, present it, and **stop**. Wait for "approve", or for a
   cut/add/change. After "approve": `rmo approve`.
2. **Tasks.** Fill `tasks.md`, Session 1, with a short checklist. Each line has a
   file and a criterion ID. Keep the docs task.
3. **Implement inline** (no `rmo-coder`). Follow `rmo-conventions`. One test per
   criterion, with the ID first in its docstring. Tick each box when its code is
   written and its focused test ran (`rmo check tests -- <test file>`).
4. **Gate A.** `rmo gate-a --session 1 --log "Gate A"`. Fix until it is green.
5. **Gate B.** Follow `rmo-review`: route the lenses, spawn `rmo-reviewer`
   (and `rmo-deep-reviewer` in parallel on `Risk: high`), adjudicate, show
   one ordered list, log the round with `rmo log`.
6. **Fix.** Fix the confirmed findings. After a fix, run `rmo gate-a` again.
   Re-review only the delta, and only after a confirmed Critical or High. Ask the
   developer about each finding you propose to accept, and record accepted
   trade-offs in `.claude/rmo/reviewer-memory.md` when they say so.
7. **Close.** When both gates are green: `rmo-finish`.

A spec that turns out wrong is a spec delta (`rmo-spec`), never a quiet change.
