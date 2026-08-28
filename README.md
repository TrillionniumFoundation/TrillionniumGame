# TrillionniumGame

`TrillionniumGame` is the Trillionnium Foundation program to reimplement the complete Nakama OSS game backend in Rust.

## Status

**Audit-refined planning baseline v2. No compatibility, drop-in-replacement or production claim exists yet.**

The current GitHub repository is still named `Trillionnium-Nakama`; its file tree and project identity have transitioned to TrillionniumGame while the repository-name administrative mutation remains pending. Existing Git history, branches and pull requests are retained as migration evidence.

Initial compatibility target:

- Nakama `v3.40.0`
- Nakama commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`
- nakama-common `v1.47.0`
- nakama-common commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`

The program covers HTTP/gRPC/WebSocket compatibility, authentication and sessions, accounts, storage and search, social graphs, chat and presence, notifications, leaderboards, tournaments, matchmaking, parties, relayed and authoritative multiplayer, runtime modules, IAP/subscriptions, Console, migrations, operations, security, performance, and full cutover evidence.

The final production server will contain no first-party Go service code. Existing Go runtime modules must be migrated to Rust/WASM. Compiled Go plugin ABI compatibility is not a final-product goal, so `drop-in` claims are scoped by the compatibility profiles rather than implied globally.

## Start here

- [Current plan v2](CURRENT_PLAN.md)
- [Plan audit](docs/development/PLAN_AUDIT_2026-08-28.md)
- [Project boundary](PROJECT_BOUNDARY.md)
- [Full-Rust ADR](docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md)
- [Parity denominator specification](docs/development/PARITY_DENOMINATOR_SPEC.md)
- [Feature parity roll-up](docs/development/FEATURE_PARITY_MATRIX.md)
- [Execution model](docs/development/PROGRAM_EXECUTION_MODEL.md)
- [Stage gates](docs/development/CRITICAL_PATH_AND_STAGE_GATES.md)
- [Product gates](docs/status/PRODUCT_GATES.json)

Validate planning artifacts with:

```bash
python3 scripts/check-plan.py
```

## License and attribution

The repository is licensed under Apache License 2.0. Compatibility work is based on study of the Apache-2.0-licensed Nakama OSS project. See [NOTICE](NOTICE) and the upstream baseline metadata for attribution and exact source identities.
