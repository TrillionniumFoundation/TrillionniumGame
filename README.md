# TrillionniumGame

`TrillionniumGame` is the Trillionnium Foundation program to reimplement the complete Nakama OSS game backend in Rust.

## Current status

**Planning baseline only. No production-readiness or compatibility claim exists yet.**

The first compatibility target is:

- Nakama `v3.40.0`
- Nakama commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`
- nakama-common `v1.47.0`
- nakama-common commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`

The program covers HTTP/gRPC/WebSocket compatibility, authentication and sessions, accounts, storage and search, social graphs, chat and presence, notifications, leaderboards, tournaments, matchmaking, parties, relayed and authoritative multiplayer, runtime modules, IAP/subscriptions, Console, migrations, operations, security, performance, and full cutover evidence.

The final production server will contain no first-party Go service code. Existing Go runtime modules must be migrated to Rust/WASM; compiled Go plugin ABI compatibility is not a final-product goal because retaining a Go loader would violate the full-Rust boundary.

## Start here

- [CURRENT_PLAN.md](CURRENT_PLAN.md)
- [Project boundary](PROJECT_BOUNDARY.md)
- [ADR-0001](docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md)
- [Feature parity matrix](docs/development/FEATURE_PARITY_MATRIX.md)
- [Machine-readable backlog](docs/development/EXECUTION_BACKLOG.json)
- [Product gates](docs/status/PRODUCT_GATES.json)

Validate planning artifacts with:

```bash
python3 scripts/check-plan.py
```

After an authenticated GitHub CLI with organization repository-creation access is available, publish this exact local repository with:

```bash
bash scripts/publish-repository.sh
```

The script refuses to publish a dirty tree, re-runs the plan contract, creates a private `TrillionniumFoundation/TrillionniumGame` repository, and pushes the exact local `main` commit.

## License and attribution

The repository is licensed under Apache License 2.0. Compatibility work is based on study of the Apache-2.0-licensed Nakama OSS project. See [NOTICE](NOTICE) and the upstream baseline metadata for attribution and exact source identities.
