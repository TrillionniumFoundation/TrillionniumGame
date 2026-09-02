# trnm-token-crypto-provider

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-crypto-provider`  
Workspace class: `isolated`  
Lifecycle: `security-critical-crypto-provider`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-crypto-provider`. Its authority is limited to the module boundary described here: **opaque key-operation provider boundary**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Resolve domain/key/epoch handles, select active epochs, bound verification epochs, revoke, and report provider health.

Non-goals: It does not define JWT format, expose raw production keys to application code, or substitute a real KMS/HSM deployment.

## Architecture and dependencies

JWT and token services call opaque operations through this boundary. Local deterministic providers are development-only profiles.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Unknown domain or epoch fails closed, provider calls have deadlines, raw key bytes are not returned unnecessarily, and revoke is monotonic.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Active/verification epoch transitions are deterministic, bounded, cache-aware, and safe across node convergence.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Production requires a reviewed secret manager, KMS, or HSM; key-domain reuse, logging, test-key promotion, and fallback are forbidden.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-crypto-provider/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-crypto-provider/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-crypto-provider/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Expose provider latency, cache refresh, active epoch, verification fallback count, revoke, health, and deadline failures without secret labels.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Real provider integration, rotation/revoke convergence, failure injection, independent security review, and penetration evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P1-REVIEW-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
