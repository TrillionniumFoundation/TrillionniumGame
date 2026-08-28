# Trillionnium Nakama

Independent real-time match authority for Trillionnium.

The active P0 foundation implements a fixed two-participant authoritative match,
durable command/event evidence, restart/resume, authenticated archive catch-up,
and Nakama-signed `MatchCompletedV1` evidence. Product gameplay rules remain in
World; evaluation and authorization policy remain in Hepta; consensus, ingress,
finality and proofs remain in Chain; wallet and ledger settlement remain in CEX.

The bounded cross-repository transition slice consumes the exact
`trnm_world_transition_v1` contract. Nakama prepares requests from an already
authoritative context and independently verifies unsigned World state, replay,
outcome and transition hashes. World never receives participant, roster,
global-order, idempotency, private-key, finality or wallet authority.

## Current development

Authoritative runtime:

- contract: `contracts/AUTHORITATIVE_MATCH_V1.md`;
- public schemas: `contracts/v1/`;
- deterministic evidence vectors: `contracts/golden-vectors.json`;
- Go runtime: `runtime/`;
- operational gates: `scripts/check-nakama-p0.sh`.

World transition consumer:

- design: `docs/WORLD_TRANSITION_V1_ADAPTER.md`;
- shadow runbook: `docs/WORLD_TRANSITION_V1_SHADOW_RUNBOOK.md`;
- exact World lock: `contracts/world-transition-v1-consumer-lock.json`;
- delivery status: `contracts/world-transition-v1-adapter-status.json`;
- independent Python implementation: `runtime/world_transition_v1/`;
- accepted/rejected fixture emitter:
  `tools/emit_world_transition_v1_shadow_fixture.py`.

The transition consumer is not authority cutover. Live World execution must use
a separate prepare → execute → verify → stale-fenced commit path so no external
work occurs while an authoritative lock or storage transaction is held.

This is not a production or release claim. P0 remains single-host, exact-head
remote checks and Integration evidence are still required, and public online
and public player markets remain disabled.

## Start work

1. Work only from `/home/alex/projects/trillionnium-nakama`.
2. Run `bash scripts/project-preflight.sh`.
3. Create a focused `feature/nakama-*` branch.
4. Define or update a versioned contract before coupling another repository.
5. Never depend on a sibling working tree; pin exact repository revisions and
   vendored contract blobs.

Run the authoritative P0 acceptance gate with:

```bash
bash scripts/check-nakama-p0.sh
```

World game-server code must not be migrated wholesale. Extract one
Nakama-owned capability at a time behind explicit contracts, invariant tests,
persistence boundaries and rollback procedures.
