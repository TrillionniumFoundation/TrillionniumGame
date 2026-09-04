# trnm-persistence-pg

Status: **module documentation; http-grpc-websocket-database-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-persistence-pg`  
Workspace class: `root`  
Lifecycle: `product-adapter-and-temporary-composition`  
Owner role: `database-migration`

## Status and authority

This document is the current module-level engineering contract for `trnm-persistence-pg`. Its authority is limited to the module boundary described here: **authoritative PostgreSQL/CockroachDB adapter and temporary database-backed server binary**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `http-grpc-websocket-database-source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Serializable persistence, CAS, receipts, outbox rows, database profiles, pool policy, migrations, and the current database-backed vertical slice.

Non-goals: Its embedded server location is temporary and must not become a permanent coupling between process supervision and persistence.

## Architecture and dependencies

It implements core repository contracts and composes selected token, session, realtime-wire, HTTP, gRPC, WebSocket, and migration components.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

The only production DDL authority is migrations/. PostgreSQL and CockroachDB are separate profiles with separate evidence and retry behavior.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Commit/replay acknowledgement, revision and generation fencing, bounded serializable retry, response-loss recovery, and outbox terminal behavior are mandatory.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Production database transport requires verify-full TLS and reviewed credential providers. Plaintext is limited to explicit loopback development evidence.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-persistence-pg --all-targets --locked
cargo clippy --package trnm-persistence-pg --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## TLS test endpoint readiness

The `pg-tls-rotation` live lane must not accept the initialization server's Unix socket as the final endpoint. `scripts/wait-postgresql-tls-ready.py` connects explicitly to container TCP `127.0.0.1` with `sslmode=verify-full` and the profile's read-only mounted root certificate. It executes SQL against the requested test database and requires `ssl=on`, a non-recovery server, and TLS on its own `pg_stat_ssl` session. A transient SQL failure retries inside one monotonic deadline; a stopped or unavailable container fails. Empty, extra, non-TLS or failed-query output never earns readiness, even if the process prints `ready`.

The default total budget is 60 seconds per endpoint, with a validated maximum of 300 seconds. Each subprocess receives at most three seconds and never more than the remaining budget. libpq connection and SQL statement budgets are separately two seconds. A success arriving at or after the total deadline is rejected. Subprocess diagnostics, password values and connection URLs are not emitted by the helper. The ephemeral test password is forwarded by the environment variable name, not embedded in subprocess argument values. Published container ports bind only to host loopback.

```bash
python3 -m unittest tests.control_plane.test_pg_tls_endpoint_readiness -v
```

The deterministic suite exercises initialization transition, failed SQL, actual command construction, stopped containers, per-operation and total timeouts, late success, invalid input and diagnostic redaction. It is a mocked prerequisite regression, not a live database or TLS-rotation result. The separate Rust probe must still execute old/new-root success, cross-root rejection and invalid-root rejection. The workflow manifest requires both source/unit and live execution jobs; a successful unit job cannot substitute for a skipped live job. This repair changes no authoritative DDL, public API, receipt, rollback authority or production claim.

## Operations

Pools, acquisition, statements, locks, transactions, retries, readiness, drain, outbox, and profile identity require bounded metrics and failure reasons.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Pool/TLS cancellation, persistent realtime, complete gRPC/gateway, session integration, outbox delivery, HA/PITR, load, SDK, and oracle evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-DATA-001`
- `GAP-P1-PG-001`
- `GAP-P1-TEST-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
