# ADR-0001: Full Rust Reimplementation of the Nakama OSS Server

- Status: Accepted as planning baseline
- Date: 2026-08-28
- Decision owners: TrillionniumGame Maintainers
- Applies to: `TrillionniumFoundation/TrillionniumGame`

## Context

Trillionnium requires ownership of a complete game-backend platform rather than a narrow custom runtime plugin. The selected baseline is Nakama OSS `v3.40.0` and its matching `nakama-common v1.47.0` protocol/runtime definitions.

A partial adapter or a Rust computation library embedded in a Go server would retain Nakama as the platform authority. That does not satisfy the requested full rewrite. A complete replacement must own public protocols, data semantics, runtime hosting, realtime coordination, Console, migrations, operations and release evidence.

## Decision

Build a full behavioral reimplementation in Rust with the following rules:

1. First-party production server code is Rust.
2. External API and data compatibility is measured against the pinned upstream baseline.
3. The final product contains no Go server or Go sidecar.
4. Rust-native and WASM modules are first-class runtime targets.
5. Lua and JavaScript/TypeScript compatibility are implemented by Rust-hosted engines.
6. Existing Go runtime source must be migrated to Rust/WASM before cutover.
7. Compiled Go plugin ABI compatibility is intentionally not provided in the final product because it would require retaining Go runtime behavior and ABI coupling.
8. PostgreSQL and CockroachDB compatibility are validated independently.
9. A one-owner rule applies to sessions, parties, matches, schedulers, purchases and durable writes during migration.
10. Nakama is retired only after full parity gates, migration validation, canary and rollback rehearsals pass.

## Consequences

Positive:

- Complete control of memory safety, concurrency, scheduling and deployment.
- Unified Rust ecosystem with Trillionnium World and other Rust components.
- Stable opportunity to design explicit capability boundaries and evidence-driven compatibility.
- Ability to introduce WASM module isolation and stronger resource governance.

Costs and risks:

- This is a multi-year platform program, not a plugin port.
- Runtime language compatibility and realtime scheduling are high-risk workstreams.
- Existing Go modules require source migration.
- Console must be rebuilt in Rust/WASM.
- Database and protocol behavior contain hidden edge cases that require an oracle and differential harness.
- Upstream evolution must be tracked while the first baseline is implemented.

## Rejected alternatives

### Keep Nakama and rewrite only custom logic in Rust

Rejected because Nakama remains the server, protocol, persistence and realtime authority.

### Rust dynamic library called from a Go plugin

Rejected as the final architecture because it retains the Go plugin ABI and operational complexity.

### Rewrite only the features currently used by Trillionnium World

Rejected because the program requirement is full Nakama OSS parity.

### Exact compiled Go plugin compatibility

Rejected for the final product. Source migration and behavior compatibility are provided instead.

## Verification

This decision is complete only when the `TrillionniumGame 1.0` acceptance matrix in `CURRENT_PLAN.md` is fully satisfied and Nakama has been drained and retired from production authority.
