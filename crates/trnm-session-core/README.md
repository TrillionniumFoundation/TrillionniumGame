# trnm-session-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-session-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `identity`

## Status and authority

This document is the current module-level engineering contract for `trnm-session-core`. Its authority is limited to the module boundary described here: **session-family state model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Refresh-family state, atomic rotation, replay detection, revocation boundaries, and access/socket invalidation intents.

Non-goals: It does not parse JWTs, store session rows, disconnect sockets, or expose public network endpoints.

## Architecture and dependencies

The core consumes verified identities and emits deterministic state transitions; persistence and realtime adapters implement durable storage and fanout.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Concurrent refresh cannot create two valid successors. Replay of a consumed refresh token revokes the family and requires connected-session invalidation.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Family ownership is singular, rotation is atomic, revocation is monotonic, and unknown state fails closed without revealing account existence.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

No raw token or signing key is logged. Token parsing and cryptographic verification occur in reviewed adapters before invoking this model.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-session-core --all-targets --locked
cargo clippy --package trnm-session-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Adapters must expose refresh, replay, revoke, fanout, and failure metrics with bounded cardinality and auditable event identifiers.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Database repository, server middleware, socket revocation fanout, migration ownership, and immutable-oracle differential remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P0-SERVER-001`
- `GAP-P0-DATA-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
