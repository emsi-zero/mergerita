# Lens: query — database cost and lock scope

- **Ceiling:** High · **Min confidence:** 0.7
- **Reads:** the diff, the models and migrations it touches, and `reviewer-memory.md`
  (small query costs on the panel are accepted at prototype scale).

## Checklist — answer every item

1. **N+1** — no loop, template, admin `list_display` or `__str__` crosses a relation
   per row without `select_related` or `prefetch_related`.
2. **Query in a loop** — no per-row `.get()`, `.exists()` or `.count()` where one
   annotated query works.
3. **Boundedness** — worker, retry and reconcile querysets are bounded or iterate
   in batches. Panel lists are scoped to one person.
4. **Lock scope** — `select_for_update` locks only the rows it needs. Use `of=`
   with joins and `skip_locked` for worker claims. No lock is held across a loop
   that could call out.
5. **Index coverage** — new filters on growing tables (inbox, outbox, audit,
   assignments, sessions) have an index or a constraint that covers them.
6. **Constraints** — "one open X" is a partial `UniqueConstraint` with a
   `condition` (LOCK-5), and its name is listed under Contracts.
7. **Migration cost** — no table rewrite or long lock on inbox, outbox or audit.
   A data migration iterates in batches when the table can be large.
8. **Raw SQL** — only the advisory-lock calls, and they are parameterized.
