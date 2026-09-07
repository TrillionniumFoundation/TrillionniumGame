# trnm-presence-router-v2

Status: **module documentation; integration-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-presence-router-v2`  
Workspace class: `isolated`  
Lifecycle: `realtime-integration-candidate`  
Owner role: `realtime-distributed-systems`

## Status and authority

This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to deterministic in-process presence, connection ownership, session-generation fencing, and bounded revocation fanout. Source presence or a passing unit suite does not establish distributed delivery, durable revocation, Nakama compatibility, or production acceptance.

## Responsibilities

The module owns:

- deterministic route deltas and hidden/visible transitions;
- exact connection-generation ownership fencing;
- exact session ID and session-generation binding for every mutating operation;
- monotonic per-session revocation high-water;
- atomic removal of all bound connections at or below a revoked session generation;
- stale session and connection re-entry rejection;
- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;
- fail-closed invariant checking before and after staged mutations.

It does not own network transport, identity-provider issuance, durable revocation storage, cluster membership, cross-node fanout, reconnect orchestration, or actual socket termination.

## Architecture

`PresenceRouter` remains the connection-generation route state machine. `SessionRouteRegistry` composes it with:

```text
connection -> session ID, session generation, connection generation
session ID -> bounded active connection set
session ID -> monotonic revoked-through generation
```

Every applied registry mutation is executed against a cloned candidate and committed only after route, index, generation, and configured-capacity invariants pass. The complete state is finite; cloning cost is therefore bounded by the configured admitted universe rather than historical unbounded input.

`PresenceRouter` retains connection-generation high-water state after route removal. `SessionRouteRegistry` consequently has separate active-connection and tracked-connection limits. Removing a connection releases active capacity, while a new connection identity is still rejected once the tracked-connection universe is full. Reusing the same admitted identity requires a strictly newer connection generation.

Revocation high-waters are security state and are not opportunistically evicted. Their configured hard limit is a finite admitted-session universe. Deployments requiring compaction must first establish a durable external generation floor and add a separately reviewed compaction protocol; this source candidate deliberately fails closed instead of deleting a fence.

## Public contracts

`SessionJoinRequest` binds a `JoinPresenceRequest` to a positive `SessionRouteGeneration`.

The other mutation paths are deliberately session-fenced wrappers:

- `SessionUpdateRequest`;
- `SessionLeaveRequest`;
- `SessionRemoveConnectionRequest`.

Each wrapper carries the expected session ID and exact session generation in addition to the exact connection generation. A delayed operation from session generation `N` cannot mutate, leave, or disconnect a connection already rebound in place to generation `N+1`.

`SessionRevocationRequest` advances one session's revoked-through generation. It removes all indexed connections whose session generation is less than or equal to that value. Repeating the same or an older revocation is exactly idempotent. A newer bound generation survives.

`SessionRouteLimits::new` validates six positive finite limits and rejects values above repository hard maxima:

```text
connections per session
active connections
tracked connection identities
tracked sessions
revocation high-waters
presence entries
```

Default and hard maxima are exported as constants. Resource exhaustion is a stable, no-mutation error. Public serialized fields, configuration mappings, and externally observable error classes are change-controlled.

## Correctness and failure model

The registry verifies that:

- every active binding matches the router's exact connection generation and identity;
- every binding appears in exactly its session index;
- no session index is empty or above its configured fanout limit;
- no active binding is at or below its revocation high-water;
- active, tracked, session, revocation, and presence cardinalities stay within configured limits;
- all underlying presence invariants remain valid.

Capacity checks run before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, or exhausted operations leave route state, indexes, revisions, and deltas unchanged.

The implementation is an in-memory state machine. Process restart, durable replay, cross-node ordering, and network partitions remain adapter-level obligations.

## Security and privacy

Route mutation requires authenticated project/session context supplied by a trusted adapter. Session generation is an authorization fence, not a user-controlled counter. Cross-project state leakage is forbidden.

Secrets, raw tokens, user payloads, receipts, and provider credentials must not appear in logs or metric labels. Revocation high-water is an enforcement input; it is not proof that a durable identity authority issued the revocation.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-presence-router-v2/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-presence-router-v2/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-presence-router-v2/Cargo.toml --all-targets --locked -- -D warnings
```

The focused corpus covers:

- visible and hidden multi-connection revocation;
- stale re-entry and newer-generation survival;
- stale update, leave, and remove after in-place generation advance;
- connection/session identity conflicts;
- exact duplicate revocation;
- active and tracked connection saturation;
- unique zero-connection revocation exhaustion;
- per-session and presence-entry exhaustion;
- no mutation on rejection and safe active-capacity reuse inside the admitted identity universe;
- invalid or incoherent limit configuration.

The isolated workspace must also execute in the stable aggregate gate. Empty discovery, skipped mandatory tests, warnings, stale-head results, and local-only execution receive no evidence credit.

## Operations and evidence

Adapters must expose bounded cardinalities, generation mismatch rejection, revocation fanout, reconnect outcomes, capacity saturation, and invariant failures without high-cardinality labels. Readiness, drain behavior, durable revocation replay, and recovery must be specified before production use.

Evidence must bind the exact repository, source commit/tree, prospective merge object, workflow/run/job/attempt, environment, assertions, retained artifact digests, limitations, expiry, and independent review.

Blocking gaps include `GAP-P0-SERVER-001`, `GAP-P1-IDENTITY-001`, `GAP-P0-CI-001`, and `GAP-P1-REVIEW-001`. Durable session-store integration, actual socket closure, distributed fanout, reconnect recovery, differential compatibility, capacity/endurance, and conflict-free specialist review remain required.
