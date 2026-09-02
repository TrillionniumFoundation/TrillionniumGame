# trnm-canonical-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-canonical-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `protocol`

## Status and authority

This document is the current module-level engineering contract for `trnm-canonical-core`. Its authority is limited to the module boundary described here: **canonical framing primitives**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Bounded deterministic canonical framing, normalization boundaries, and byte-stable domain representations.

Non-goals: It does not implement complete generated API/RTAPI bindings, network listeners, cryptography, or persistence.

## Architecture and dependencies

Protocol adapters use these primitives before transport and persistence. The crate must remain deterministic and side-effect free.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Canonical output is byte-stable for the same validated input. Limits, ordering, escaping, and invalid-input behavior are compatibility-sensitive.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

No ambiguous encoding, unbounded allocation, locale dependence, or hidden normalization is allowed.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

All lengths and nesting are bounded. Canonicalization never turns unverified input into an authenticated identity or permission decision.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-canonical-core --all-targets --locked
cargo clippy --package trnm-canonical-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

No runtime service is owned. Consumers report parser/framing failures without payload or identity leakage.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Generated API/RTAPI adapters, official SDK consumers, and immutable-oracle byte differential remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SCOPE-001`
- `GAP-P0-SERVER-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
