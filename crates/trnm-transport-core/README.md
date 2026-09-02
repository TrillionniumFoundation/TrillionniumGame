# trnm-transport-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-transport-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `protocol`

## Status and authority

This document is the current module-level engineering contract for `trnm-transport-core`. Its authority is limited to the module boundary described here: **stable domain-to-transport error mapping**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Stable mapping of domain failures to HTTP, gRPC, realtime error, retry, and close classifications.

Non-goals: It does not parse requests, accept sockets, implement TLS, or decide domain authorization.

## Architecture and dependencies

Protocol adapters translate validated requests into services and use this crate only to render stable public failure semantics.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Public codes, status classes, retryability, Retry-After behavior, details, and socket-close mapping are compatibility-sensitive.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Internal causes cannot alter stable public classifications unexpectedly. Unknown failures fail closed and are not exposed verbatim.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Error output must not leak tokens, SQL, database URLs, provider details, or private authorization reasons.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-transport-core --all-targets --locked
cargo clippy --package trnm-transport-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Adapters emit low-cardinality result metrics keyed by stable operation and public status, never by user or payload.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Complete network adapters, headers/details, retry metadata, close semantics, and oracle differential remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-SCOPE-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
