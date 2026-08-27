# Trillionnium Nakama

Independent real-time multiplayer authority for Trillionnium World.

This repository owns authenticated match admission, rooms, presence,
authoritative match lifecycle, canonical global event order, command
idempotency/recovery and replay/event-root production. Product gameplay rules
remain in World; evaluation policy remains in Hepta; finality and proofs remain
in Chain; wallet/ledger settlement remains in CEX.

## Current development

The current bounded cross-repository slice is the Nakama adapter for
`trnm_world_transition_v1`:

- adapter design: `docs/WORLD_TRANSITION_V1_ADAPTER.md`;
- shadow runbook: `docs/WORLD_TRANSITION_V1_SHADOW_RUNBOOK.md`;
- exact World source lock:
  `contracts/world-transition-v1-consumer-lock.json`;
- machine-readable delivery state:
  `contracts/world-transition-v1-adapter-status.json`;
- implementation: `runtime/world_transition_v1`;
- exact accepted/rejected fixture emitter:
  `tools/emit_world_transition_v1_shadow_fixture.py`.

This adapter verifies unsigned World game-domain state, replay and outcome
material inside a pre-existing Nakama authority context. It does not yet
implement the deployed Nakama runtime module, persistent global ordering,
restart recovery, canonical roots or `MatchCompletedV1` signing.

Public online and public player markets remain disabled.

## Start work

1. Work only from `/home/alex/projects/trillionnium-nakama`.
2. Run `bash scripts/project-preflight.sh`.
3. Create a focused `feature/nakama-*` branch.
4. Define or update a versioned contract before coupling another repository.
5. Never depend on a sibling working tree; pin exact repository revisions and
   vendored contract blobs.

World game-server code must not be migrated wholesale. Extract one Nakama-owned
capability at a time behind explicit contracts, tests and rollback boundaries.
