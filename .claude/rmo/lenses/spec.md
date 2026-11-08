# Lens: spec — the diff against the approved spec

- **Ceiling:** High · **Min confidence:** 0.7
- **Reads:** `spec.md` in full, `design.md` when present, the diff. Run
  `.claude/rmo/bin/rmo trace --no-record [--session N]` instead of grepping test names.

## Checklist — answer every item

1. **Criteria implemented** — name the `file:line` that implements each criterion.
2. **Criteria tested** — `rmo trace` is green: exactly one test per ID, ID first in the docstring.
   A test asserts the observable result the criterion names (state, row, audit event,
   outbox job, status code), not a helper call.
3. **Nothing extra** — no behaviour, setting, admin action, URL, outbox kind or
   audit action that the spec does not state. A test with no criterion is a finding (Low),
   except the LOCK-6 concurrency tests named in the spec's invariants.
4. **Non-goals held** — one line per non-goal. In particular: no merge call (GL-3),
   no new panel write path for MR or booking data (AUTH-4).
5. **Invariants have a mechanism** — every "Holds" ID and every INV row has a
   lock, constraint or transaction in the diff or in the existing code that keeps it.
6. **Contracts complete** — every migration, audit action, outbox kind and payload,
   chat or alert text, GitLab field, admin action, panel URL, setting and env var in the diff
   is listed under Contracts. A missing entry here caused documentation-only deltas in MRG and RVO.
7. **Docs** — the `Docs` section is done: `docs/testing.md` scenario (letter), README, smoke items.
8. **Spec still true** — when the code shows a criterion is wrong, raise
   `Spec delta needed: <rows>`. The work stops until the developer decides.
