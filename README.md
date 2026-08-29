# TrillionniumGame

`TrillionniumGame` is the Trillionnium Foundation program to reimplement the complete Nakama OSS game backend in Rust.

## Current status

The repository contains substantial **source-level implementation candidates** for authority sequencing, sessions, canonical framing, storage, persistence/outbox, token policy and JWT compatibility, query parsing, transport errors, presence routing and separate PostgreSQL/CockroachDB foundations. Selected database slices have relay-produced evidence, but evidence targeting an older commit or lacking the v3 evidence/review contract earns no current-candidate claim credit.

The currently runnable repository path is still official Nakama `v3.40.0` loading the first-party Go plugin under `runtime/`. That Go module is a migration input and compatibility oracle. The repository does not yet contain an end-to-end first-party Rust server binary that accepts and durably completes the declared Nakama protocol surface.

This repository **does not yet claim complete Nakama compatibility, C1–C5, production readiness, public-online approval, drop-in replacement, or Nakama retirement**. Empty, absent, skipped, cancelled, older-head or unreviewed checks/evidence do not change that boundary.

Initial compatibility baseline:

- Nakama `v3.40.0`
- Nakama commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`
- nakama-common `v1.47.0`
- nakama-common commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`

## Plan v3 execution control

Plan v3 separates immutable scope from mutable execution state and makes gap/gate promotion evidence-driven:

- [`CURRENT_PLAN.md`](CURRENT_PLAN.md) — binding full-scope plan and closure rules;
- [`docs/status/CURRENT_STATE.json`](docs/status/CURRENT_STATE.json) — current fail-closed state snapshot;
- [`docs/status/GAP_REGISTER.json`](docs/status/GAP_REGISTER.json) — P0/P1/P2 gaps and exact close criteria;
- [`docs/status/EXECUTION_STATUS.json`](docs/status/EXECUTION_STATUS.json) — workstream and stage execution state;
- [`docs/status/IMPLEMENTATION_INVENTORY.json`](docs/status/IMPLEMENTATION_INVENTORY.json) — source-to-capability/test/evidence map;
- [`docs/evidence/index.json`](docs/evidence/index.json) — evidence registry and target-identity boundary;
- [`docs/status/PRODUCT_GATES.json`](docs/status/PRODUCT_GATES.json) — evidence-derived product gates;
- [`docs/roadmap/NEXT_MILESTONE.json`](docs/roadmap/NEXT_MILESTONE.json) — current blocker-first execution queue.

The first critical path is repository-native CI/governance, one database schema authority, security/durability source fixes and the first end-to-end Rust server vertical slice. Broad domain expansion may not bypass those dependencies.

## Architecture and engineering contracts

- [Current and target runtime](docs/architecture/CURRENT_AND_TARGET_RUNTIME.md)
- [Rust server reference architecture](docs/architecture/RUST_SERVER_REFERENCE_ARCHITECTURE.md)
- [Parity denominator specification](docs/development/PARITY_DENOMINATOR_SPEC.md)
- [Compatibility divergences](docs/development/COMPATIBILITY_DIVERGENCES.json)
- [Schema authority](docs/development/SCHEMA_AUTHORITY.json)
- [Test and verification policy](docs/testing/TEST_POLICY.md)
- [Cryptography and key lifecycle](docs/security/CRYPTOGRAPHY_AND_KEYS.md)
- [Branch and merge policy](docs/governance/BRANCH_AND_MERGE_POLICY.md)
- [Security reporting policy](SECURITY.md)

## Validation

Run the complete offline control-plane contract:

```bash
python3 scripts/check-plan.py
```

Core source preflight:

```bash
cargo fmt --all -- --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The JWT compatibility adapter and other standalone workspaces are intentionally checked by the aggregate merge gate even while they remain outside the root workspace. A local pass is development feedback, not remote or compatibility evidence.

## License and attribution

The repository is licensed under Apache License 2.0. Compatibility work is based on the Apache-2.0-licensed Nakama OSS project. See [`NOTICE`](NOTICE) and the pinned upstream metadata for attribution and exact source identities.
