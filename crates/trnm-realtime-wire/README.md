# trnm-realtime-wire

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-realtime-wire`  
Workspace class: `isolated`  
Lifecycle: `realtime-wire-compatibility-candidate`  
Owner role: `protocol`

## Status and authority

This document is the current module-level engineering contract for `trnm-realtime-wire`. Its authority is limited to the module boundary described here: **bounded realtime JSON/protobuf envelope candidate**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Realtime envelope, opcode, CID, bounded payload, JSON/protobuf conversion, and stable wire error primitives.

Non-goals: It is not a persistent socket server, distributed connection registry, session authenticator, or full RTAPI implementation.

## Architecture and dependencies

WebSocket and realtime adapters consume this crate; it must remain independent of database and process ownership.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Opcode, envelope fields, CID correlation, binary/text profile, payload bounds, and close/error semantics are compatibility-sensitive.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Malformed, duplicate, oversized, or unsupported frames fail deterministically without desynchronizing the connection state.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

All frames are untrusted. Parser bounds, fuzzing, authentication-before-mutation, and redacted failures are mandatory.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-realtime-wire/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-realtime-wire/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-realtime-wire/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Consumers expose frame totals, parse failures, close reasons, queue pressure, reconnects, and profile identity.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Complete RTAPI messages, persistent lifecycle, official SDK differential, fuzz, and distributed behavior remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-SCOPE-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
