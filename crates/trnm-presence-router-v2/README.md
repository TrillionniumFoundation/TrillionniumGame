# trnm-presence-router-v2

Status: **module documentation; integration-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-presence-router-v2`  
Workspace class: `isolated`  
Lifecycle: `realtime-integration-candidate`  
Owner role: `realtime-distributed-systems`

## Status and authority

This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to the module boundary described here: **extended route and ownership candidate**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `integration-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Route deltas, ownership generations, black-box vector behavior, and explicit aggregate-gate integration.

Non-goals: It does not own a network runtime, cluster membership, durable registry, or production multi-node fanout.

## Architecture and dependencies

It builds as an isolated workspace and is intended to converge with the stable presence/realtime service boundary.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Route ownership changes are generation-fenced, bounded, deterministic, and observable through stable deltas.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Stale owners cannot update current routes; duplicate deltas are idempotent and queue/memory use is bounded.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Route and presence visibility require authenticated project/session context. Cross-project state leakage is forbidden.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-presence-router-v2/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-presence-router-v2/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-presence-router-v2/Cargo.toml --all-targets --locked -- -D warnings
```

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Future adapters must expose owner, generation, route count, stale rejection, fanout, reconnect, and queue saturation.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Network runtime, multi-node fault evidence, Nakama differential, and independent review remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
