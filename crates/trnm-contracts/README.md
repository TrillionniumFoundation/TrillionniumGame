# trnm-contracts

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-contracts`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `protocol`

## Status and authority

This document is the current module-level engineering contract for `trnm-contracts`. Its authority is limited to the module boundary described here: **shared domain contract authority**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Stable domain codes, retry classes, bounded identifiers, digests, receipts, and shared command/result vocabulary.

Non-goals: It does not own HTTP, gRPC, WebSocket framing, persistence, process lifecycle, or generated upstream APIs.

## Architecture and dependencies

This is a leaf-level shared crate. Higher layers may depend on it; it must not depend on transport, database, or server composition crates.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Public Rust types are compatibility-sensitive. Code, retry class, identifier bounds, and serialization changes require downstream impact review.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Identifiers and digests remain bounded and strongly typed. Error/retry classification must be deterministic and must not expose private implementation details.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Treat identifiers, receipts, and digests as untrusted input. Do not place secrets or raw credentials in shared contract types or Debug output.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-contracts --all-targets --locked
cargo clippy --package trnm-contracts --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

The crate has no runtime process or background worker. Operational impact is indirect through consumers and is assessed through compatibility and integration tests.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Current coverage is a source candidate. Full generated public protocol binding and immutable-oracle differential evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CI-001`
- `GAP-P0-SCOPE-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
