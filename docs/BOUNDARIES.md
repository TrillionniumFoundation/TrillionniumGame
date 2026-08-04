# Boundaries

Nakama accepts versioned player/session commands and emits versioned match and
replay events. It may call World-owned rule interfaces and publish evidence for
Hepta/Integration consumers, but it must not import sibling source trees.

Nakama is authoritative for room membership, presence, match lifecycle, event
ordering, and the replay-event root. World remains authoritative for gameplay
rules and campaign state. Hepta remains authoritative for evaluation,
eligibility, and settlement policy. Chain remains authoritative for canonical
state, proofs, and finality.
