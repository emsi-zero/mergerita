# LOG: Orchestrator thin-slice prototype

<!-- Append-only. One entry per gate result, review round, or spec delta.
     Never rewrite an entry. Newest last. -->

## 2026-10-26 — spec created
Route: project · Key: RMO

## 2026-10-26 10:42 UTC — spec approved
sha256 51178d5aca0e

## 2026-10-26 — spec delta: checkpoint command
Changed: checkpoint `docker compose run --rm web pytest -q` → `scripts/test.sh -q` (host venv + local postgres:14 over a unix socket).
Why: Docker Hub pulls return 403 and containers have no DNS on this machine, so the compose image cannot be built. Dockerfile and docker-compose.yml are kept but unverified.
Approved by the developer (checkpoint question, "Host venv + local Postgres").

## 2026-10-26 11:05 UTC — spec delta approved
sha256 749c11cd486f

## 2026-10-26 — Session 1 Gate A
tests `scripts/test.sh -q` PASS (4 passed, 3 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 1` PASS (4/4)

## 2026-10-26 — Session 1 Gate B
reviewer (spec, style, query): COMMENT, no Critical/High. deep-reviewer (logic, security): COMMENT, no Critical/High.
Fixed: public-class docstrings on invariant owners and the view; typing on audit overrides; INV-6 ORM bypasses (`base_manager_name`, `bulk_create(update_conflicts)`); safe settings defaults (DEBUG off, SECRET_KEY required, ALLOWED_HOSTS from env); fail-closed case added to the RMO-1 test.
Deferred, needs developer decision (spec delta): malformed Merge Request Hook body (non-dict, missing project.id / object_attributes.iid) gives 500, not 400; invalid-JSON 400 branch is outside the contract; header values over 64 chars raise DataError.
Deferred: missing-UUID 400 has no test (validation decision, not a criterion).
Kept: `.pylintrc` docstring disables (AppConfig and test classes would all need docstrings).
Session 1: done.

## 2026-10-26 — spec delta: RMO-20 malformed webhook → 400
Added RMO-20; Contracts now list 400; validation decision "Payload shape" changed from NOT ADDED to REQUIRED for the fields ingress reads. Planned in Session 2 (T6).
Why: both Session 1 reviewers found a 500 on malformed MR hooks. Approved by the developer ("Add delta: reply 400").

## 2026-10-26 11:13 UTC — spec delta approved
sha256 8772d8b728e9

## 2026-10-26 — spec delta: RMO-21 poison-event quarantine
Added RMO-21: failing event → rolled back, marked failed, admin alert, worker continues; later events of that MR wait until an admin clears it. Planned in Session 3 (T6b), because the alert needs the outbox.
Why: Session 2 coder found an unknown author (or any handler error) leaves the event pending, so the worker crashes each pass and the MR is blocked. Approved by the developer ("Quarantine + admin alert").

## 2026-10-26 11:20 UTC — spec delta approved
sha256 40437e1411fe

## 2026-10-26 — Session 2 Gate A
tests `scripts/test.sh -q` PASS (8 passed, 10 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 2` PASS (4/4)

## 2026-10-26 — Session 2 Gate B
reviewer (spec, style, query): APPROVE. deep-reviewer (logic, security): COMMENT, no Critical/High.
Fixed: [Medium] RMO-6 commit-order gap (a later delivery committed before an earlier one) → claim only events older than `RMO_INBOX_CLAIM_GRACE_SECONDS` (2 s), asserted in the RMO-6 test. [Low] non-integer/null project.id or iid → 400. [Low] NaN/Infinity and deep nesting → 400.
Deferred to Session 3: [Medium, conf 0.5] partial index on pending `id` for the claim's outer ORDER BY (planned in T6b). Poison events (RMO-21, T6b).
Deferred: NUL / lone-surrogate JSON strings still give a 500 from jsonb (GitLab does not send them). Low style notes (docstrings on Command, `dict[str, object]`, length 64 copied from the model).
Note: `run_worker --once` leaves events younger than the grace pending.
Session 2: done.

## 2026-10-26 — spec delta: Session 3 review
RMO-7: fail label wins when both labels are present. RMO-10: the combined owner must not be the MR author. RMO-11: an MR with no changed files also goes to triage. Contracts: settings list completed (backoff, lease, claim grace, working days, gateway, notifier). design.md: M2M through-table FK exception recorded.
Why: Session 3 reviewer and deep-reviewer findings. Approved by the developer (empty MR → triage; exclude author; fail wins).

## 2026-10-26 11:47 UTC — spec delta approved
sha256 8fb1a7765ed9

## 2026-10-26 — Session 3 Gate A
tests `scripts/test.sh -q` PASS (15 passed, 22 subtests) · pylint 10.00 PASS · isort PASS · black PASS · `craft-spec trace --session 3` PASS (7/7)

## 2026-10-26 — Session 3 Gate B
reviewer (spec, style, query): COMMENT, no Critical/High. deep-reviewer (logic, security): COMMENT, no Critical/High, no security findings.
Fixed: [Medium] outbox batch lease + unfenced result writes → one claim at a time, results fenced on the held lease; [Medium] reclaim after lease expiry did not count an attempt → attempts counted at claim, exhausted leases fail; [Medium] empty change list stalls MR → triage (delta RMO-11); [Medium] quarantine raced after lock release → conditional failed mark, alert only on count 1; [Medium] combined owner could be the author → excluded (delta RMO-10); [Medium] Protocol/handler docstrings; [Low] fail-wins recorded and tested (delta RMO-7); [Low] RMO-21 test now proves rollback; [Low] FOR UPDATE OF self; [Low] unmatched append; Contracts settings list completed; M2M FK exception recorded in design.md.
No re-review: no Confirmed Critical/High finding (workflow §3); fixes verified by Gate A.
Deferred: verdict labels already present on the open event are ignored until the next update; ~4 queries per area group; blocked/failed inbox events are re-scanned each poll (production note).
Session 3: done.
