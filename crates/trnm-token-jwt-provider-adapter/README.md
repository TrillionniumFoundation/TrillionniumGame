# trnm-token-jwt-provider-adapter

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-jwt-provider-adapter`  
Workspace class: `isolated`  
Lifecycle: `security-critical-provider-adapter`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-jwt-provider-adapter`. Its authority is limited to the module boundary described here: **bridge between JWT compatibility semantics and opaque provider operations**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Adapt strict JWT issue/verify behavior to domain-separated provider handles and epoch selection.

Non-goals: It is not a KMS implementation, general JWT library, authorization service, or session repository.

## Architecture and dependencies

It composes token policy, strict format validation, and the crypto-provider interface while remaining an isolated mandatory gate target.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Algorithm and domain are fixed, unknown epochs never fall back, verification epochs are bounded, and unverified payloads never authenticate.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Provider failure, malformed token, wrong epoch/domain, and signature mismatch are deterministic fail-closed results.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Raw keys, tokens, claims, and provider locations are redacted. Rotation, revoke, cache invalidation, and node convergence require evidence.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Expose issue/verify result, epoch, provider latency/health, fallback rejection, cache refresh, and revoke metrics at bounded cardinality.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Server integration, real KMS/HSM, malformed/fuzz corpus, rotation/revoke evidence, and independent cryptography review remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P1-REVIEW-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
