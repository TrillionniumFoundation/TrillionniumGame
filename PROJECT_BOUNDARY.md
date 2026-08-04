# Trillionnium Nakama Boundary

- Project ID: `trillionnium-nakama`
- Canonical root: `/home/alex/projects/trillionnium-nakama`
- Lane: `nakama-realtime`
- Lifecycle: bootstrap

## Owns

Matchmaking, room lifecycle, presence, authoritative match state, match event
ordering, and replay-event-root production.

## Does not own

World gameplay/campaign rules, Hepta evaluation or settlement, Chain
finality/runtime, CEX ledger state, or cross-repository orchestration. Interfaces
must be versioned contracts and test fixtures, not sibling source paths.
