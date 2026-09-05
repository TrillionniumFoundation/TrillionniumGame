# trnm-token-jwt-adapter-gate

Status: **module documentation; source-gate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-jwt-adapter-gate`  
Workspace class: `isolated`  
Lifecycle: `test-gate`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-jwt-adapter-gate`. Its authority is limited to the module boundary described here: **first compatibility-adapter source and vector gate**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-gate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Compile and exercise security-critical adapter vectors independently of root workspace membership.

Non-goals: It is not a product library, public API, token provider, or production deployment artifact.

## Architecture and dependencies

It depends on the JWT adapter and is registered as an aggregate-gate isolated workspace.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

A missing target, omitted matrix lane, skipped test, warning, or vector failure is a hard merge failure.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

The gate must execute non-empty deterministic assertions and preserve negative cases rather than normalizing malformed input.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

It exists to prevent omission of security-critical code. It must not contain production keys, secrets, or bypass-only logic.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-jwt-adapter-gate/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-jwt-adapter-gate/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-jwt-adapter-gate/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

No runtime operation exists. CI duration, assertion count, and failure identity are its only operational outputs.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Passing this gate grants source coverage only; it does not grant cryptographic, compatibility, or production acceptance.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
