# trnm-token-jwt-adapter-gate-v2

Status: **module documentation; source-gate-v2; no automatic compatibility or production credit**  
Path: `crates/trnm-token-jwt-adapter-gate-v2`  
Workspace class: `isolated`  
Lifecycle: `test-gate`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-jwt-adapter-gate-v2`. Its authority is limited to the module boundary described here: **second hostile/source-shape gate retained during adapter convergence**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-gate-v2`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Exercise an additional independently registered gate lane for the JWT adapter's current source boundary.

Non-goals: It is not a second JWT implementation, product dependency, release artifact, or permanent versioning strategy.

## Architecture and dependencies

It depends on the compatibility adapter and must remain represented in package authority and aggregate CI until convergence is reviewed.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

The lane is mandatory while registered. Duplicate package names are allowed only by explicit package authority.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

The gate must fail on missing source, omitted targets, malformed vectors, warning regressions, or empty execution.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

No production secret or private primitive is introduced here. The gate exists solely to make omission impossible.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

No service is owned. The convergence plan should delete or merge this gate once equivalent coverage is proven.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

It grants no compatibility credit by itself and requires an explicit removal decision when superseded.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
