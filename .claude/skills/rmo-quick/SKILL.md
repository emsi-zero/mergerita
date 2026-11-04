---
name: rmo-quick
description: "Quick route of the merge-orchestrator workflow: a typo, copy text, template markup, docs, a rename or a one-line fix with a known cause. It is implemented inline with no spec and no reviewer subagent. Use it when the user runs /rmo-quick or the rmo router picks quick."
argument-hint: <the small change>
---

# rmo-quick

1. Confirm it is quick (WORKFLOW.md §1). It must not touch a lock, a transaction,
   the inbox or outbox, the gateway, the webhook, panel auth or scoping, or a
   migration. If it does, stop, say why, and switch to `rmo-task`.
2. Make the change in this session. Follow `rmo-conventions` for code.
3. Run only the focused checks through `rmo`, for example:
   - `.claude/rmo/bin/rmo check tests -- panel/tests/test_views.py`
   - `.claude/rmo/bin/rmo check lint -- panel/views.py`
   - `.claude/rmo/bin/rmo check format` and `check imports`
   - for docs and templates only: format and imports are enough. Say so.
4. Read your diff once against `.claude/rmo/lenses/style.md`.
5. Report the diff summary, each check with its result, and anything from the
   style read. No spec, no `.claude/specs/` entry, no subagent. When the developer
   says "review it", spawn `rmo-reviewer` with the `style` lens and `git diff`.
