# Lens: logic — correctness under concurrency, retries and GitLab reality

- **Ceiling:** Critical · **Min confidence:** 0.6
- **Reads:** the spec's criteria and invariants, `.claude/rmo/INVARIANTS.md` (the
  families the spec lists under "Holds", plus LOCK and TX always), the diff, and the
  callers and states it touches. `design.md`'s "Write paths and locks" table when present.

Every item below matches a defect class that past reviews here confirmed as High.
Probe each one with a concrete interleaving or input, not a general worry.

## Checklist — answer every item

1. **Requirement** — each criterion behaves as stated at its boundary: midnight
   Asia/Tehran, the last window in the horizon, a limit of 0, an empty list.
2. **Lock order (LOCK-1)** — list the locks each new write path takes, in order.
   Is any taken in reverse, twice, or after `lock_booking()`?
3. **Read after lock (LOCK-2)** — is every eligibility or state decision read
   *after* the MR lock is held, in its own statement? Is any `before` value for
   audit read under the lock?
4. **Two callers** — run the same command twice at once: the webhook and the
   reconcile event, two approvals, the retry and a manual retry, two workers. Is
   the lock or the partial unique constraint (LOCK-5) that prevents the double
   effect present? Is an `IntegrityError` from it handled as "already done"?
5. **Transaction shape (TX-1, TX-2, TX-3)** — one atomic block per command. The audit
   event and outbox rows are inside it. No GitLab or network call is inside it.
   No chat is sent directly.
6. **GitLab truth and freshness (GL-1, GL-2, GL-4)** — is any decision taken from a
   hook payload instead of a GitLab read? Can an approval from before a push, a
   revoked approval, or an old bot verdict count? Can a stale snapshot roll back a
   newer `head_sha`, `web_url` or state?
7. **Idempotency and the lease (IO-1, IO-3)** — can the handler run twice (an
   outbox retry, a lease expiry, a re-delivered hook) with a single effect? Is a
   result written only under the lease fence?
8. **Poison and partial failure (IO-2)** — a malformed field, an unknown person,
   a non-GitLab exception, one bad MR in a batch. Does it quarantine, alert and move
   on? Does any path leave an MR stuck (for example in `awaiting_booking`) with no retry?
9. **State machine** — every new transition comes from a valid state, writes
   audit, and has a way out. An MR never moves backwards past `approved` or `merged`.
10. **Separation of duties (SEP-1, SEP-2)** — the author is never booked or made
    merge owner, and a reviewer is never made merge owner of the same MR.
11. **Time (DATA-2)** — no naive datetime and no `date.today()`. "Today" is
    `timezone.localdate()`.
12. **Migration (DATA-3)** — `db_default` on new non-null fields. A data migration
    has a noop reverse, and the spec says if the worker must stop. Is a constraint
    added to a populated table safe against existing duplicates?
13. **Tests** — each test fails without the change. Concurrency tests are
    `TransactionTestCase` with two threads, and they fail with the lock removed
    (LOCK-6). A missing test for a transition, a race or scoping is a finding at its
    consequence. Framework-behaviour, duplicate and implementation-detail tests are Low.
14. **Validation** — each added check is REQUIRED or USEFUL and lives at the owning
    layer: model or constraint for data, service for rules, view for input shape.
    A redundant re-check is Low.
