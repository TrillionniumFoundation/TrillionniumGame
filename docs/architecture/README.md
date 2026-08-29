# Architecture index

## Current binding architecture

- [`CURRENT_AND_TARGET_RUNTIME.md`](CURRENT_AND_TARGET_RUNTIME.md) — separates today's Nakama+Go-plugin topology from the target first-party Rust server and defines migration authority by phase.
- [`RUST_SERVER_REFERENCE_ARCHITECTURE.md`](RUST_SERVER_REFERENCE_ARCHITECTURE.md) — process composition, request flow, persistence/outbox, protocol, realtime, runtime, lifecycle and test boundaries for the first vertical slice.
- [`../development/SCHEMA_AUTHORITY.json`](../development/SCHEMA_AUTHORITY.json) — one production-authoritative migration chain and adapter ABI.
- [`../development/MIGRATION_AUTHORITY_MATRIX.md`](../development/MIGRATION_AUTHORITY_MATRIX.md) — one-writer authority across migration phases.
- [`../security/CRYPTOGRAPHY_AND_KEYS.md`](../security/CRYPTOGRAPHY_AND_KEYS.md) — cryptography, token and key-provider architecture.

## Foundational decisions

- [`../adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md`](../adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md)
- [`../adr/ADR-ROADMAP.md`](../adr/ADR-ROADMAP.md)
- [`../development/ORACLE_AND_DIFFERENTIAL_SPEC.md`](../development/ORACLE_AND_DIFFERENTIAL_SPEC.md)
- [`../development/CAPACITY_AND_SLO_SPEC.md`](../development/CAPACITY_AND_SLO_SPEC.md)

## Historical narrow slices

`WORLD_COMMAND_DEPLOYED_RUNTIME_CONTEXT_V1.md` and related WorldCommand documents remain scoped historical architecture records. They do not describe the complete current or target TrillionniumGame server.

Trillionnium Chain and World gameplay rules remain outside this repository's owned architecture boundary.
