# Boundaries

Nakama accepts versioned player/session commands and emits versioned match and
replay events. It may call World-owned deterministic rule interfaces and
publish evidence for Hepta/Integration consumers, but it must not import sibling
source trees.

## Nakama owns

- authenticated match admission and authorization;
- participant membership, roles and presence;
- match version and lifecycle;
- canonical global command/event sequence;
- command idempotency and restart recovery;
- canonical event/roster/archive roots;
- canonical `MatchCompletedV1` construction and signing.

## World transition boundary

The pinned `trnm_world_transition_v1` interface supplies only:

- exact ruleset/content interpretation;
- deterministic next-state material;
- unsigned replay material;
- optional unsigned outcome material;
- World request/transition/outcome hashes.

Before a World request is constructed, Nakama must already own an immutable
authority context. The adapter may derive opaque retry-stable correlation IDs
from that context, but it must not send participant, roster, global-order,
idempotency, private-key, finality or wallet authority into World payloads.

A World transition/outcome hash is not participant admission, canonical order,
archive completeness, completion signature, Chain finality or settlement
evidence.

## Other systems

- World remains authoritative for gameplay rules and campaign/game-domain
  state.
- Hepta remains authoritative for evaluation and eligibility policy.
- Chain remains authoritative for canonical state, proofs and finality.
- CEX remains authoritative for wallet/ledger mutation and custody.
- Integration remains authoritative for exact cross-repository component locks
  and E2E evidence.

The current World adapter is shadow-only. It cannot enable public online,
public markets or canonical authority cutover by itself.
