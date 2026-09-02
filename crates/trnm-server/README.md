# trnm-server

Status: **module documentation; standalone-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-server`  
Workspace class: `isolated`  
Lifecycle: `server-foundation-prototype`  
Owner role: `foundation-runtime`

## Status and authority

This document is the current module-level engineering contract for `trnm-server`. Its authority is limited to the module boundary described here: **foundation process prototype; not the canonical production binary**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `standalone-source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

The exact no-credit source boundary consumed by the server vertical-slice gate is:

```text
compatibility_credit=false
database_durability_credit=false
sg4_credit=false
production_ready=false
```

## Responsibilities

Typed configuration, bounded ingress, worker supervision, health/readiness, drain, and composition-root process contracts.

Non-goals: While the database-backed temporary authority exists, this package is not the canonical trnm-server release binary and grants no production authority.

## Architecture and dependencies

It composes bounded foundation behavior and must eventually replace the temporary binary atomically with source, tests, lockfile, status, and gate updates.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Process admission, deadlines, readiness, abnormal child exit, and drain are shared across HTTP, gRPC, and upgraded WebSockets.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

No new mutation is admitted after drain acknowledgement; admitted work is bounded; worker panic/error converges to failure and unready state.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Non-loopback exposure is explicit, request sizes are bounded, errors are redacted, and production TLS/auth remain separate acceptance requirements.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Expose low-cardinality health, readiness reason, worker, queue, request, failure, and shutdown-phase signals.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Database durability, complete protocols, session integration, load, HA, SDK/oracle differential, and production extraction remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P1-PG-001`
- `GAP-P0-CI-001`
- `GAP-P1-REVIEW-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
