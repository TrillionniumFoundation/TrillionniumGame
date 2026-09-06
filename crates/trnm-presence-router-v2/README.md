# trnm-presence-router-v2

Status: **module documentation; integration-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-presence-router-v2`  
Workspace class: `isolated`  
Lifecycle: `realtime-integration-candidate`  
Owner role: `realtime-distributed-systems`

## Status and authority

This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to the module boundary described here: **extended route, connection ownership, and session-revocation candidate**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `integration-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

The module owns deterministic in-process primitives for:

- route deltas and visibility changes;
- connection-generation ownership fencing;
- session-generation binding for every active connection;
- monotonic per-session revocation high-water;
- atomic removal of every connection at or below a revoked session generation;
- stale-session and stale-connection re-entry rejection;
- bounded per-session connection indexing;
- black-box vector behavior and explicit aggregate-gate integration.

Non-goals: It does not own a network runtime, cluster membership, durable registry, cross-node fanout, provider token validation, or production socket transport. A caller must durably establish the session revocation before using it to terminate live transports.

## Architecture and dependencies

`PresenceRouter` remains the connection-generation route state machine. `SessionRouteRegistry` composes it with two additional indexes:

```text
connection -> session ID, session generation, connection generation
session ID -> bounded active connection set
session ID -> monotonic revoked-through generation
```

Every mutating registry operation applies to a cloned candidate and commits only after route and index invariants pass. Session revocation therefore either removes the complete eligible connection set and advances the high-water, or leaves the prior state unchanged. A revocation through generation `N` cannot remove a connection already rebound to generation `N+1`, and generation `N` cannot rejoin after the high-water is recorded.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Route ownership changes are generation-fenced, bounded, deterministic, and observable through stable deltas.

`SessionJoinRequest` binds a presence join to one positive `SessionRouteGeneration`. For one unchanged connection generation, the session identity cannot change and the session generation cannot regress. A higher connection generation may establish a new identity, while the old connection generation remains fenced.

`SessionRevocationRequest` advances one session's revoked-through generation. It removes all currently indexed connections whose session generation is less than or equal to that value. Repeating the same or an older revocation is exactly idempotent. The default source bound is `MAX_CONNECTIONS_PER_SESSION = 1024`.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Stale owners cannot update current routes; duplicate deltas are idempotent; stale session generations cannot rejoin; a revocation cannot partially remove a session; and queue/memory use is bounded by the declared wrapper limits.

The registry verifies that:

- every active connection binding matches the router's exact connection generation and identity;
- every binding appears in exactly the corresponding session index;
- no session index is empty or exceeds its connection limit;
- no active binding is at or below its session revocation high-water;
- all underlying presence invariants remain valid.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Route and presence visibility require authenticated project/session context. Cross-project state leakage is forbidden. Revocation high-water is an enforcement input, not proof that an identity provider or durable session store issued the revocation.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-presence-router-v2/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-presence-router-v2/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-presence-router-v2/Cargo.toml --all-targets --locked -- -D warnings
```

The session-revocation corpus covers multi-connection visible/hidden removal, stale re-entry, newer-generation survival, same-socket session-generation advance, connection-generation takeover, identity conflict, stale generation rejection, exact duplicate revocation, and invariant preservation.

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Future adapters must expose owner, connection generation, session generation, revocation high-water, route count, stale rejection, disconnect fanout, reconnect, and queue saturation.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Durable session issuance/revocation, live WebSocket closure, reconnect cursor, network runtime, multi-node fault evidence, Nakama differential, and independent review remain open. The source registry does not claim that a real socket was closed or that a remote node observed the revocation.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P1-IDENTITY-001`
- `GAP-P0-CI-001`
- `GAP-P1-REVIEW-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Durable session-store integration, actual socket-disconnect execution, distributed fanout, reconnect recovery, and production capacity/endurance remain separate gates.
