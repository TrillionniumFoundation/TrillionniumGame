# Boundaries

Nakama accepts versioned player/session commands and emits versioned match and
replay events. It may call World-owned rule interfaces and publish evidence for
Hepta/Integration consumers, but it must not import sibling source trees.

Nakama is authoritative for room membership, presence, match lifecycle, event
ordering, and the replay-event root. World remains authoritative for gameplay
rules and campaign state. Hepta remains authoritative for evaluation,
eligibility, and settlement policy. Chain remains authoritative for canonical
state, proofs, and finality.

For P0, Hepta supplies signed immutable authorizations and agent-key snapshots;
Nakama durably consumes them and emits a signed completion. Nakama does not
reimplement Hepta scoring or accept caller-provided roots. The terminal event
contains terminal facts but no derived root, preventing self-reference.

Open-source Nakama match instances are single-host and in-memory. This lane
persists logical state in server-owned storage and resumes it into a new
external match instance after restart. That is not multi-host fencing. A
storage-version conflict fails closed.

Integration owns cross-repository orchestration and must remain blocked until
Chain canonical ingress/finality receipts and Hepta signed authorization are
available at immutable compatible revisions.
