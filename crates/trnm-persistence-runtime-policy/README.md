# trnm-persistence-runtime-policy

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-persistence-runtime-policy`  
Workspace class: `isolated`  
Lifecycle: `database-runtime-policy-candidate`  
Owner role: `database-migration`

## Status and authority

This document is the current module-level engineering contract for `trnm-persistence-runtime-policy`. Its authority is limited to the module boundary described here: **bounded pool, TLS, timeout, retry, and cancellation policy model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Configuration and validation rules for database pool sizes, timeouts, TLS modes, retry budgets, jitter, and cancellation expectations.

Non-goals: It does not execute SQL, own connection pools, rotate certificates, or prove profile behavior by itself.

## Architecture and dependencies

Persistence/server adapters consume the policy only after translating it into profile-specific runtime behavior.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

All acquisition, statement, lock, idle-transaction, retry, and shutdown budgets are finite and validated.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Retries obey attempt and elapsed deadlines, invalid combinations fail closed, and policy cannot permit unbounded resources.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Production profiles require verify-full TLS and reviewed credential references; plaintext is explicit loopback-only development behavior.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-persistence-runtime-policy/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-persistence-runtime-policy/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-persistence-runtime-policy/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Consumers expose effective redacted policy identity, pool saturation, timeout, cancellation, retry, and TLS health metrics.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Live cancellation, certificate rotation, deadlock/restart, saturation, security, and performance evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P1-PG-001`
- `GAP-P0-DATA-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
