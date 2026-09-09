# trnm-authority-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-authority-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `realtime-distributed-systems`

## Status and authority

This document is the current module-level engineering contract for `trnm-authority-core`. Its authority is limited to the module boundary described here: **authority generation and revision model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Generation fencing, revision sequencing, command identity boundaries, and stale-owner rejection primitives.

Non-goals: It is not a distributed lease service, placement service, network registry, or durable database implementation.

## Architecture and dependencies

The domain model must remain transport- and database-independent. Adapters translate storage clocks, leases, and process ownership into these primitives.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Generation and revision are monotonic strong types. A stale generation or stale revision must never be accepted as an authoritative mutation.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Single-writer safety, deterministic takeover, idempotent duplicate handling, and fail-closed stale-owner behavior are the primary invariants.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Authority receipts and command identities are security-sensitive. Verification happens before state transition, and secret key material stays outside this crate.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-authority-core --all-targets --locked
cargo clippy --package trnm-authority-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

No network listener is owned here. Metrics should be emitted by adapters for takeover, stale-write rejection, and generation changes.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Distributed lease, node-loss, partition, and Nakama behavior differential remain required before production or broad compatibility credit.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
