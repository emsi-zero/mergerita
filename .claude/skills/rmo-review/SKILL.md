---
name: rmo-review
description: "Review protocol for merge-orchestrator: route lenses, spawn rmo-reviewer (and rmo-deep-reviewer on Risk high), adjudicate findings, log the round. Reviewer agents load it as their contract (severity, confidence, checklist, output format). Use it for Gate B, when the user runs /rmo-review, or asks for a review of the current diff."
argument-hint: [diff command, default: git diff main...HEAD plus working tree]
---

# rmo-review

Lens files: `.claude/rmo/lenses/{spec,style,logic,security,query}.md`.
Routing: WORKFLOW.md §3. Suppressions: `.claude/rmo/reviewer-memory.md`.

## A. Main session — run a round

1. **Route.** Pick the lenses with WORKFLOW.md §3 from the changed paths
   (`git diff --name-only`). `spec` and `style` always run on task and project.
2. **Spawn** in one message, in parallel:
   - `rmo-reviewer`: spec dir, lenses (minus logic and security on `Risk: high`),
     diff command.
   - On `Risk: high`, also `rmo-deep-reviewer`: spec dir, diff command, and the
     invariant families from the spec's "Holds".
   - "full review" from the developer: one `rmo-reviewer` per lens instead.
3. **Adjudicate.** Never forward raw output.
   - Critical and High: open the cited line and confirm the interleaving or input.
     Label each `Confirmed` or `Plausible` (and say what you could not verify).
   - Medium and Low: verify when it is cheap. Otherwise the fix is the check.
   - Drop what the code contradicts, and say how many in one line. Merge duplicates.
   - Resolve every `unclear` checklist item, or carry it as an open question.
   - `Spec delta needed` stops the fixes. Follow `rmo-spec` "Spec delta".
   - Decision: REQUEST_CHANGES when any Confirmed Critical or High is open,
     COMMENT for Medium and Low only, APPROVE otherwise.
4. **Show** one list, Critical → Low: one line each, with the suggestion for
   Critical and High, and the trade-offs you propose to accept.
5. **Log.** `rmo log "Gate B round N" "<reviewers and decisions>" "<counts and each High with its state>" "Accepted: ..."`

Re-review: only the reviewer that raised a confirmed Critical or High, only on
the diff since its review, with the finding list to verify.

## B. Reviewer contract (rmo-reviewer, rmo-deep-reviewer)

- **Yardstick:** the spec first, then INVARIANTS.md, then the lens checklist.
- **Checklist first, then findings.** Answer every item of every lens:
  `pass — file:line`, `n/a — <fact>`, `fail` (with a finding), or
  `unclear — <what you would need to read>`. Walk the checklist before you judge
  the change as a whole.
- **Severity starts at Medium.** Critical and High need `Justification:` naming
  the concrete path to production harm (the interleaving, the input, the request).
  Never exceed the lens ceiling. Naming and docstrings are at most Medium.
- **Confidence 0.0–1.0.** Report findings below the threshold too, marked
  `(below threshold)`. Inflating confidence is a review defect.
- **Evidence:** cite `file:line` on added or changed lines. Only what this diff
  introduces. Confirm every hypothesis in code. One defect, one lens (the
  higher-ceiling one). No praise. Do not report formatting, imports or pylint issues.
- Honor `suppress` entries in reviewer-memory unless their condition no longer holds.

Output:

```
## Checklist — <lens>
- <item> — pass — reviews/merge_router.py:88
- <item> — n/a — no migration in the diff

## Findings
[High] logic reviews/merge_retry.py:104 — eligibility read before the MR lock
Confidence: 0.8
Justification: LOCK-2; the retry and an approval revoke interleave → a revoked MR gets an owner.
Explanation: ...
Suggestion: ... ; test: two-thread TransactionTestCase under MRDR-5.

## Summary
<2–4 sentences>
Decision: APPROVE | REQUEST_CHANGES | COMMENT
```

On a re-review: for each given finding, `fixed`, `not fixed` or `partly fixed`
with `file:line`, then the lenses applied to the delta only.
