# trnm-token-jwt-adapter

Status: **module documentation; length-fix-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-jwt-adapter`  
Workspace class: `isolated`  
Lifecycle: `security-critical-compatibility-adapter`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-jwt-adapter`. Its authority is limited to the module boundary described here: **strict HS256 compatibility-format adapter**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `length-fix-source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Strict JWT segment, base64url, JSON, UTF-8, algorithm, signature-length, legacy/epoch route, and claim validation.

Non-goals: It is not a production key store, KMS/HSM client, general JWT library, or authorization policy owner.

## Architecture and dependencies

The adapter consumes token policy and opaque provider operations. Isolation is not a waiver; the aggregate gate must always execute it.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Exactly three segments, allowed algorithms only, strict duplicate-field rejection, exact 32-byte HS256 signature, and no unknown-epoch fallback.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Unequal lengths, including a 256-byte delta in either argument order, reject without truncation. Malformed input cannot reach trusted claims.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

This is security-critical hand-written compatibility code and receives no production approval without vectors, fuzzing, dependency review, and independent cryptography review.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-jwt-adapter/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-jwt-adapter/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-jwt-adapter/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

No secrets or raw tokens are logged. Consumers expose bounded validation-result metrics and provider health separately.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Known-answer vectors are necessary but not sufficient. KMS integration, malformed/fuzz exact-head evidence, and accepted security decision remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P1-CRYPTO-002`
- `GAP-P1-REVIEW-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
