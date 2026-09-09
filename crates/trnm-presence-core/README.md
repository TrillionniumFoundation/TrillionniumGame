# trnm-presence-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-presence-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `realtime-distributed-systems`

## Status and authority

This document is the current module-level engineering contract for `trnm-presence-core`. Its authority is limited to the module boundary described here: **presence identity and local lifecycle model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Presence identities, join/leave cleanup, route generations, bounded queues, and stale-route rejection primitives.

Non-goals: It is not a persistent WebSocket server, distributed registry, cross-node bus, or reconnect protocol implementation.

## Architecture and dependencies

Realtime adapters and routers consume this deterministic core; network and cluster effects remain outside the transaction-free model.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Every presence has an owner generation and bounded lifecycle. Leave, disconnect, drain, and owner loss must converge without ghost state.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Stale generation cannot mutate current presence. Queue admission and slow-consumer policy are bounded and deterministic.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Presence visibility follows authenticated session and stream ACL decisions. User data must not become metric labels.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-presence-core --all-targets --locked
cargo clippy --package trnm-presence-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Adapters expose joins, leaves, stale-route rejection, queue depth, drops, disconnects, reconnects, and owner changes.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Persistent/distributed WebSocket integration, registry, fanout, reconnect cursor, failover, load, and oracle evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-SCOPE-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
