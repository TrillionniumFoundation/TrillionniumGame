# trnm-token-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-core`. Its authority is limited to the module boundary described here: **token policy and key-epoch domain model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Claim bounds, issuer/audience/time policy, key domains, active and verification epochs, and rotation/revoke decisions.

Non-goals: It does not implement cryptographic primitives, parse JWT wire format, contact KMS/HSM, or integrate HTTP middleware.

## Architecture and dependencies

Reviewed token-format and provider adapters supply verified claims and opaque key operations to this policy layer.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Unknown epochs never fall back. Algorithm, domain, issuer, audience, lifetime, skew, subject, username, variables, and token IDs are bounded.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Policy evaluation is deterministic and fail closed. Rotation must not silently extend token lifetime or reuse key domains.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Access, refresh, console, runtime, socket, authority, provider, and evidence key domains remain distinct.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-token-core --all-targets --locked
cargo clippy --package trnm-token-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Provider and server adapters expose rotation, verification, unknown-epoch, revoke, and health metrics without token data.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Production key provider, server/session integration, node convergence, rotation/revoke evidence, and independent review remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P0-SERVER-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
