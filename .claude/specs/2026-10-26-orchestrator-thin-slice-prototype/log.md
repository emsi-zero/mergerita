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
