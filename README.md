# TrillionniumGame

`TrillionniumGame` is the Trillionnium Foundation program to reimplement the complete Nakama OSS game backend in Rust.

## Current status

The repository has a consolidated development history. The current implementation contains audited Rust foundations for authority sequencing, sessions, canonical framing, storage OCC, persistence/outbox semantics, token policy and JWT compatibility boundaries, query parsing, transport errors, presence routing, database profiles, and the remaining parity/oracle control plane.

This repository **does not yet claim complete Nakama compatibility, production readiness, public-online approval, or Nakama retirement**. Those claims remain controlled by the stage and product gates in the development plan.

Initial compatibility baseline:

- Nakama `v3.40.0`
- Nakama commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`
- nakama-common `v1.47.0`
- nakama-common commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`

## Start here

- [Current development plan](CURRENT_PLAN.md)
- [Plan audit](docs/development/PLAN_AUDIT_2026-08-28.md)
- [Project boundary](PROJECT_BOUNDARY.md)
- [Full-Rust ADR](docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md)
- [Parity denominator specification](docs/development/PARITY_DENOMINATOR_SPEC.md)
- [Stage gates](docs/development/CRITICAL_PATH_AND_STAGE_GATES.md)
- [Product gates](docs/status/PRODUCT_GATES.json)
- [Branch consolidation audit](docs/governance/BRANCH_CONSOLIDATION_2026-08-29.md)

Run the repository planning contract with:

```bash
python3 scripts/check-plan.py
```

## License and attribution

The repository is licensed under Apache License 2.0. Compatibility work is based on the Apache-2.0-licensed Nakama OSS project. See [NOTICE](NOTICE) and the pinned upstream metadata for attribution and exact source identities.
