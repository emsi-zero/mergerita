# TASKS: {title}

Spec: spec.md · Key: {key} · Route: {route} · State: pending
Checkpoint: `.claude/rmo/bin/rmo gate-a`

<!-- Live state. Tick a box when its code is written AND its check ran.
     Every task names a file and the criterion it serves. On the project
     route, write one "## Session N" block per session, each with a Covers: line. -->

## Session 1 — {title}

State: pending
Covers: {key}-1

- [ ] T1 — <action> in `reviews/<module>.py` — {key}-1
- [ ] T2 — test {key}-1 in `reviews/tests/test_<module>.py`
- [ ] T3 — docs: docs/testing.md scenario, README section

Gate A: `.claude/rmo/bin/rmo gate-a --session 1 --log "Session 1 Gate A"`
Gate B: rmo-reviewer (spec, style, routed lenses) · rmo-deep-reviewer when Risk: high

Resume: <files, last commit, next task>
