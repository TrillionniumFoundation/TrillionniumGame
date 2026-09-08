# trnm-presence-router-v2

Status: **module documentation; integration-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-presence-router-v2`  
Workspace class: `isolated`  
Lifecycle: `realtime-integration-candidate`  
Owner role: `realtime-distributed-systems`

## Status and authority

This document is the current module-level engineering contract for `trnm-presence-router-v2`. Its authority is limited to deterministic in-process presence, connection ownership, session-generation fencing, bounded revocation fanout, connection-actor admission, authenticated reconnect cursors and receipt-reconciled disconnect effects. Source presence or a passing unit suite does not establish distributed delivery, durable revocation, Nakama compatibility, or production acceptance.

## Responsibilities

The module owns:

- deterministic route deltas and hidden/visible transitions;
- exact connection-generation ownership fencing;
- exact session ID, session-generation and route-namespace binding for every mutating operation;
- monotonic per-session revocation high-water;
- atomic removal of all bound connections at or below a revoked session generation;
- stale session and connection re-entry rejection;
- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;
- bounded actor queues, frame bytes, pending requests and aggregate payload memory;
- generation-bound asynchronous request handles and authenticated reconnect cursors;
- receipt-reconciled disconnect effects with archive and epoch-wide verifier-receipt reservations;
- fail-closed invariant checking before and after staged mutations.

It does not own network transport, identity-provider issuance, durable revocation storage, cluster membership, cross-node fanout, reconnect orchestration, or actual socket termination.

## Architecture and dependencies

`PresenceRouter` remains the connection-generation route state machine. `SessionRouteRegistry` composes it with:

```text
connection -> session ID, session generation, connection generation
session ID -> bounded active connection set
session ID -> monotonic revoked-through generation
```

Every applied registry mutation is executed against a cloned candidate and committed only after route, index, generation, and configured-capacity invariants pass. The complete state is finite; cloning cost is therefore bounded by the configured admitted universe rather than historical unbounded input.

`PresenceRouter` retains connection-generation high-water state after route removal. `SessionRouteRegistry` consequently has separate active-connection and tracked-connection limits. Removing a connection releases active capacity, while a new connection identity is still rejected once the tracked-connection universe is full. Reusing the same admitted identity requires a strictly newer connection generation. Capacity admission computes the checked post-replacement inventory, so a higher generation that atomically retires old streams or an exclusively owned old session is accepted when it does not increase the bounded state.

Revocation and connection high-waters are never opportunistically evicted. Long-lived deployments may compact only through a quiescent namespace rollover: every mutation carries the active `SessionRouteNamespace`; a trusted verifier must accept a proof binding the current registry/router revisions, retained cardinalities, a durable checkpoint digest and a producer-barrier digest; active bindings and presence entries must be zero. The rollover advances the namespace monotonically, emits a restartable `SessionRouteCheckpoint`, resets the bounded high-water window, and permanently rejects delayed messages from retired namespaces.

### Bounded recovery layers

`ConnectionActor` independently bounds frame admission, queued egress and pending request state. Every asynchronous response, cancellation and completion requires the exact live `RequestHandle`; numeric correlation reuse cannot let a delayed callback mutate a later admission. Drain preserves only the finite request set admitted before its fence.

`ReconnectJournal` authenticates each cursor and requires one exact journal, stream, session-generation and producer-epoch identity. Replay is contiguous and bounded; expired, ahead, tampered, wrong-key and retired-generation cursors fail closed.

`DisconnectJournal` binds every possible transport write to one immutable dispatch identity. After delivery becomes ambiguous, retry is forbidden until a trusted verifier accepts a typed exact outcome. One `Unknown` result may be retained and replayed exactly; a distinct second unknown fails without mutation. Admission reserves archive capacity and the worst-case verifier-receipt allowance. Each accepted receipt consumes one reservation before owner-map mutation, terminal archive releases unused reservation, and epoch advance clears the finite namespace only after all active records have left.

`DisconnectJournalConfig` rejects zero, excessive and arithmetically unsafe limits. Construction checks the complete `tombstone_capacity × max_attempts × receipts_per_attempt` product against the repository hard cap. Production reconnect authenticators and outcome verifiers must authenticate the complete typed identity; a caller boolean or nonzero digest is never proof.

## Public contracts

`SessionJoinRequest` binds a `JoinPresenceRequest` to a positive `SessionRouteGeneration` and the exact active `SessionRouteNamespace`.

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

Capacity checks use checked projected post-transition cardinalities before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, exhausted, wrong-namespace or unverified rollover operations leave route state, indexes, revisions and deltas unchanged.

The implementation remains an in-memory state machine, but namespace retirement now has an explicit durable handoff: adapters must persist the accepted checkpoint and producer barrier and must verify the latest checkpoint before restart. Cross-node barrier construction, storage durability, ordering and network partitions remain adapter-level obligations.

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
- full-capacity same-connection generation takeover and multi-stream-to-one replacement;
- exclusive-session replacement without false tracked-session exhaustion;
- verified quiescent namespace rollover, restart restore and stale-namespace rejection;
- invalid, incoherent or unverified limit/rollover configuration;
- stale actor callbacks after correlation reuse and during drain;
- authenticated reconnect replay and cross-identity cursor rejection;
- bounded unknown outcomes, archive reservation, receipt-budget saturation and epoch recovery.

The isolated workspace must also execute in the stable aggregate gate. Empty discovery, skipped mandatory tests, warnings, stale-head results, and local-only execution receive no evidence credit.

## Operations

Adapters must expose bounded cardinalities, generation mismatch rejection, revocation fanout, reconnect outcomes, capacity saturation, and invariant failures without high-cardinality labels. Readiness, drain behavior, durable revocation replay, and recovery must be specified before production use.

Evidence must bind the exact repository, source commit/tree, prospective merge object, workflow/run/job/attempt, environment, assertions, retained artifact digests, limitations, expiry, and independent review.

## Compatibility and evidence

This source candidate preserves deterministic in-process route and recovery semantics only. Durable revocation replay, live socket closure, distributed fanout, reconnect transport, durable receipt/checkpoint persistence, Nakama differential evidence, production capacity/endurance, and conflict-free specialist acceptance remain outside this module boundary. No local source or CI result may be transferred as proof of those external properties.

## Known gaps and exit criteria

Blocking gaps include `GAP-P0-SERVER-001`, `GAP-P1-IDENTITY-001`, `GAP-P0-CI-001`, and `GAP-P1-REVIEW-001`. Durable session-store integration, actual socket closure, distributed fanout, reconnect recovery, differential compatibility, capacity/endurance, and conflict-free specialist review remain required.
