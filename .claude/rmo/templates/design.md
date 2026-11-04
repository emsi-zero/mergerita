# DESIGN: {title}

Spec: spec.md · Key: {key}

<!-- HOW, project route only. It adds no behaviour the spec does not state:
     new behaviour is a spec delta. -->

## Shape

<!-- 2-4 sentences: which reviews/ service module owns each piece, and what
     the view, worker, outbox handler and admin action do (they stay thin). -->

## Write paths and locks

<!-- One row per command that writes. Deep review checks this table first. -->
| Command (module.function) | Locks, in order (MR → booking → area review → session → capacity) | GitLab read before the transaction? | Audit action | Outbox jobs |
|---|---|---|---|---|

## Decisions

| Decision | Owner | Invariant it protects | Alternative rejected |
|---|---|---|---|

## Data and migrations

<!-- Models, fields (db_default for new non-null fields), constraints (a
     partial unique constraint for "one open X"), indexes, data migrations
     (RunPython with a noop reverse; say whether the worker must be stopped).
     Write "None." when there is none. -->

## Failure modes

<!-- For each new job, handler or batch: a GitLab error, a malformed payload,
     a crash mid-run, a second concurrent run. A poison input must never
     quarantine an MR for good or stop the whole batch. -->
| Input or fault | Behaviour | Criterion or INV |
|---|---|---|

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
