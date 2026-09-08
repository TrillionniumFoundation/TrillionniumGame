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
- exact session ID and session-generation binding for every mutating operation;
- monotonic per-session revocation high-water;
- atomic removal of all bound connections at or below a revoked session generation;
- stale session and connection re-entry rejection;
- finite admitted universes for active connections, tracked connection identities, tracked sessions, revocation high-waters, presence entries, and per-session fanout;
- bounded inbound, outbound, pending-request, frame-byte and aggregate actor payload budgets;
- actor-issued request handles that bind correlation IDs to unique admission generations;
- authenticated reconnect cursors bound to journal, stream, session generation and producer epoch;
- disconnect dispatch fencing across journal epoch, intent, operation, socket generation, attempt, worker lease and endpoint;
- an epoch-wide checked verifier-receipt budget reserved at admission and consumed before owner-map mutation;
- fail-closed invariant checking before and after staged mutations.

It does not own network transport, identity-provider issuance, durable revocation storage, cluster membership, cross-node fanout, production receipt verification, durable checkpoint custody, reconnect orchestration, or actual socket termination.

## Architecture and dependencies

`PresenceRouter` remains the connection-generation route state machine. `SessionRouteRegistry` composes it with:

```text
connection -> session ID, session generation, connection generation
session ID -> bounded active connection set
session ID -> monotonic revoked-through generation
```

Every applied registry mutation is executed against a cloned candidate and committed only after route, index, generation, and configured-capacity invariants pass. The complete state is finite; cloning cost is therefore bounded by the configured admitted universe rather than historical unbounded input.

`ConnectionActor` independently bounds frame admission, queued egress and pending request state. Every asynchronous response, cancellation and completion requires the exact live `RequestHandle`; reusing a numeric correlation ID cannot let a delayed callback mutate a later admission. Drain preserves only the finite set admitted before its fence.

`ReconnectJournal` authenticates every cursor and requires one exact journal/stream/session-generation/producer-epoch identity. Replay remains contiguous and bounded; expired, ahead, tampered, wrong-key and old-generation cursors fail closed.

`DisconnectJournal` records one immutable operation and dispatch binding per active intent. Once transport delivery may have occurred, retry is forbidden until a trusted verifier returns a typed exact outcome. One `Unknown` result may be retained and replayed exactly; a distinct second unknown fails without mutation. Admission reserves both archive capacity and the worst-case verifier-receipt allowance. Each accepted receipt consumes one reservation before it enters the owner map, terminal archive releases unused reservation, and epoch advance clears the finite receipt namespace only after all active records have left.

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

`ConnectionActorConfig`, `ReconnectJournalConfig` and `DisconnectJournalConfig` likewise reject zero, excessive and arithmetically unsafe limits. The disconnect configuration checks the complete `tombstone_capacity × max_attempts × receipts_per_attempt` product against the repository hard cap before a journal can be constructed.

Default and hard maxima are exported as constants. Resource exhaustion is a stable, no-mutation error. Public serialized fields, configuration mappings, and externally observable error classes are change-controlled.

## Correctness and failure model

The registry verifies that:

- every active binding matches the router's exact connection generation and identity;
- every binding appears in exactly its session index;
- no session index is empty or above its configured fanout limit;
- no active binding is at or below its revocation high-water;
- active, tracked, session, revocation, and presence cardinalities stay within configured limits;
- all underlying presence invariants remain valid.

Capacity checks run before cloning or allocating a candidate. Rejected stale, conflicting, ahead-of-current, revoked, exhausted, wrong-handle, wrong-cursor, wrong-dispatch or receipt-reuse operations leave route state, queues, indexes, revisions, counters and receipt owners unchanged.

The implementation is an in-memory state machine. Process restart, durable replay, authenticated production verifiers, cross-node ordering, and network partitions remain adapter-level obligations.

## Security and privacy

Route mutation requires authenticated project/session context supplied by a trusted adapter. Session generation is an authorization fence, not a user-controlled counter. Cross-project state leakage is forbidden.

Reconnect authenticators and disconnect outcome verifiers are trust-bearing interfaces. Production adapters must authenticate the complete typed identity and must not treat a nonzero digest or caller boolean as proof. Receipt ownership prevents cross-intent reuse only inside the retained epoch; durable rollover requires an authenticated checkpoint/absence protocol outside this in-memory candidate.

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
- full-capacity generation takeover using projected post-replacement inventory;
- stale actor response/cancel/complete callbacks after correlation reuse and during drain;
- authenticated contiguous reconnect replay and cross-journal/stream/session/epoch rejection;
- one bounded unknown disconnect outcome, exact idempotent replay and definitive transition;
- archive-space reservation, old-epoch dispatch rejection and terminal attempt behavior;
- aggregate verifier-receipt reservation, consumption, saturation, no-mutation failure and epoch recovery;
- invalid or incoherent limit configuration.

The isolated workspace must also execute in the stable aggregate gate. Empty discovery, skipped mandatory tests, warnings, stale-head results, and local-only execution receive no evidence credit.

## Operations

Adapters must expose bounded cardinalities, generation mismatch rejection, revocation fanout, actor saturation, cursor rejection, disconnect reconciliation outcomes, receipt-budget saturation, reconnect outcomes, capacity saturation, and invariant failures without high-cardinality labels. Readiness, drain behavior, durable revocation replay, production verifier behavior, authenticated epoch rollover, and recovery must be specified before production use.

Evidence must bind the exact repository, source commit/tree, prospective merge object, workflow/run/job/attempt, environment, assertions, retained artifact digests, limitations, expiry, and independent review.

## Compatibility and evidence

This source candidate preserves deterministic in-process route and recovery semantics only. Durable revocation replay, live socket closure, distributed fanout, reconnect transport, durable receipt/checkpoint persistence, Nakama differential evidence, production capacity/endurance, and conflict-free specialist acceptance remain outside this module boundary. No local source or CI result may be transferred as proof of those external properties.

## Known gaps and exit criteria

Blocking gaps include `GAP-P0-SERVER-001`, `GAP-P1-IDENTITY-001`, `GAP-P0-CI-001`, and `GAP-P1-REVIEW-001`. Durable session-store integration, actual socket closure, distributed fanout, reconnect recovery, differential compatibility, capacity/endurance, and conflict-free specialist review remain required.
