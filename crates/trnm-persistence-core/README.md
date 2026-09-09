# trnm-persistence-core

Status: **module documentation; outbox-terminal-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-persistence-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `database-migration`

## Status and authority

This document is the current module-level engineering contract for `trnm-persistence-core`. Its authority is limited to the module boundary described here: **database-independent transaction and outbox state model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `outbox-terminal-source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Atomic command/event/outbox semantics, exact duplicate receipts, authority and lease generations, retry and terminal dead-letter transitions.

Non-goals: It does not open database connections, execute SQL, publish provider effects, or supervise worker processes.

## Architecture and dependencies

The crate defines repository contracts consumed by services and implemented by profile-specific persistence adapters.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Acknowledgement follows durable commit or exact receipt replay. Command, event, receipt, and outbox intent form one transaction boundary.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

No partial commit, stale lease apply, stranded terminal lease, duplicate visible value effect, or acknowledged-command loss is permitted.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Receipts and fingerprints are bounded, non-secret identities. Persistence implementations must avoid leaking SQL, URLs, or payloads through public errors.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-persistence-core --all-targets --locked
cargo clippy --package trnm-persistence-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Adapters expose attempt, lease, reclaim, applied, dead-letter, and reconciliation metrics. All batches, leases, and retry loops are bounded.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Lease expiry/reclaim clock, property/model evidence, provider reconciliation, and independent data-integrity review remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P1-OUTBOX-001`
- `GAP-P0-DATA-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
