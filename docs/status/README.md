# Status index

The status directory contains current machine-readable execution and claim state. Status files must remain fail closed and may not be edited to grant evidence that does not exist.

## Current authority

- [`CURRENT_STATE.json`](CURRENT_STATE.json) — audited repository/runtime/evidence/claim snapshot.
- [`EXECUTION_STATUS.json`](EXECUTION_STATUS.json) — mutable workstream and stage-gate state over the immutable backlog.
- [`GAP_REGISTER.json`](GAP_REGISTER.json) — authoritative gap lifecycle and closure contract.
- [`IMPLEMENTATION_INVENTORY.json`](IMPLEMENTATION_INVENTORY.json) — component source/missing/evidence inventory.
- [`PRODUCT_GATES.json`](PRODUCT_GATES.json) — product gates reproduced by `scripts/derive-gates.py`.
- [`RISK_REGISTER.json`](RISK_REGISTER.json) — current risk register.
- [`SERVICE_LEVEL_OBJECTIVES.json`](SERVICE_LEVEL_OBJECTIVES.json) — provisional integrity, availability, recovery, security and capacity objectives.

## Component snapshots

- [`TRNM_SERVER_STATUS.json`](TRNM_SERVER_STATUS.json) — first-party Rust binary source, execution gaps and claim boundary.
- [`FOUNDATION_SCHEMA_STATUS.json`](FOUNDATION_SCHEMA_STATUS.json) — separate single-node schema-profile evidence.
- [`PERSISTENCE_FOUNDATION_STATUS.json`](PERSISTENCE_FOUNDATION_STATUS.json) — command/event/outbox foundation state.
- [`STORAGE_CORE_STATUS.json`](STORAGE_CORE_STATUS.json) — storage state machine and Nakama public-version source candidate.
- [`PRESENCE_ROUTER_STATUS.json`](PRESENCE_ROUTER_STATUS.json) — presence/route source candidate.
- [`QUERY_CORE_STATUS.json`](QUERY_CORE_STATUS.json) — query feasibility candidate.

Every component snapshot remains bounded by the current state, gap register and accepted evidence index. `source_candidate: true` is not remote execution, differential compatibility, database durability or production credit.

## Historical narrow status

`WORLD_COMMAND_DEPLOYED_RUNTIME_CURRENT.md` remains a historical scoped status record. It does not represent the complete TrillionniumGame server or current plan-v3 gate state.

A source-level candidate, locally passing test or relay artifact targeting an older commit cannot by itself promote C1–C5, SG1–SG9, production, public-online or retirement status.
