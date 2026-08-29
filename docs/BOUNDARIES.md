# Boundaries

Nakama accepts versioned player/session commands and emits versioned match,
archive and replay evidence. It may call World-owned deterministic rule
interfaces and publish evidence for Hepta/Integration consumers, but it must
never import sibling source trees.

## Nakama owns

- authenticated match admission and authorization consumption;
- participant membership, roles and presence;
- logical match identity, runtime generation and lifecycle;
- canonical global command/event sequence and match version;
- command idempotency, durable persistence and restart recovery;
- canonical event, roster and archive roots;
- canonical `MatchCompletedV1` construction and signing.

For P0, Hepta supplies signed immutable authorizations and agent-key snapshots.
Nakama durably consumes them and emits signed completion evidence. Nakama does
not reimplement Hepta evaluation or accept caller-provided roots. Terminal
events contain terminal facts but no derived roots, preventing self-reference.

Open-source Nakama match instances are single-host and in-memory. This lane
persists logical state in server-owned storage and resumes it into a new
external runtime generation after restart. A storage-version conflict fails
closed. This is not multi-host fencing.

## World transition boundary

The pinned `trnm_world_transition_v1` interface supplies only:

- exact ruleset/content interpretation;
- deterministic next-state material;
- unsigned replay material;
- optional unsigned outcome material;
- World request, transition and outcome hashes.

Before a World request is constructed, Nakama must already own an immutable
authority context. The adapter may derive opaque retry-stable correlation IDs
from that context, but it must not send participant, roster, global-order,
idempotency, private-key, finality or wallet authority into World payloads.

A World transition/outcome hash is not participant admission, canonical order,
archive completeness, completion signature, Chain finality or settlement
evidence. Live integration must use prepare → external execute → exact verify →
stale-fenced commit. No World or network call may run while the authoritative
core mutex or a storage/database transaction is held.

## Other systems

- World remains authoritative for gameplay rules and deterministic game-domain
  state transitions.
- Hepta remains authoritative for evaluation, authorization and eligibility
  policy.
- Chain remains authoritative for canonical state, proofs and finality.
- CEX remains authoritative for wallet/ledger mutation and custody.
- Integration remains authoritative for exact cross-repository component locks
  and E2E evidence.

Integration must remain blocked until compatible immutable World, Nakama,
Hepta and Chain artifacts and their evidence are reviewed. The current World
adapter is shadow-only and cannot enable authority cutover, public online or
public player markets by itself.
