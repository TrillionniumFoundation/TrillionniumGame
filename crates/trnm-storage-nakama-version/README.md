# trnm-storage-nakama-version

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-storage-nakama-version`  
Workspace class: `isolated`  
Lifecycle: `storage-compatibility-adapter`  
Owner role: `storage`

## Status and authority

This document is the current module-level engineering contract for `trnm-storage-nakama-version`. Its authority is limited to the module boundary described here: **exact Nakama public storage-version adapter**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Compute and validate the pinned profile's lowercase MD5 public ContentVersion over exact value bytes.

Non-goals: It is not an integrity primitive, database repository, ACL engine, storage API, or batch transaction implementation.

## Architecture and dependencies

The adapter is consumed by storage semantics and kept isolated so compatibility behavior cannot be confused with internal integrity.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

The public version is exactly 32 lowercase hexadecimal characters derived from exact stored bytes.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Byte identity, empty/wildcard/exact condition handling, malformed-version rejection, and vector reproducibility are mandatory.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

MD5 receives no collision-resistance or authentication credit. Internal integrity uses separate strong types and reviewed algorithms.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-storage-nakama-version/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-storage-nakama-version/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-storage-nakama-version/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

No runtime service exists. Storage adapters report conflict and version errors without exposing values.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Wire/database differential, ACL/batch effects, cursor behavior, and independent storage review remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P1-STORAGE-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
