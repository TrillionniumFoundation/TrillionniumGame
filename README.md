# TrillionniumGame

`TrillionniumGame` is the Trillionnium Foundation program to reimplement the complete Nakama OSS game backend in Rust.

## Current status

The repository contains substantial source and exact-head workflow candidates for authority, sessions, canonical framing, storage, persistence/outbox, JWT/token policy, query, transport errors, presence, PostgreSQL/CockroachDB foundations, a bounded database-backed HTTP/WebSocket server slice and the pinned Nakama gRPC Healthcheck signature.

The broadly proven runnable path remains official Nakama `v3.40.0` with the first-party Go plugin under `runtime/`. The Go code is migration input and an oracle fixture. The Rust server candidates do not yet implement or prove the complete Nakama HTTP/gRPC/RTAPI, Runtime, Console, provider, migration or operational denominator.

Current repository-wide claims remain:

```text
complete Nakama compatibility = false
C1-C5 = false
SG1-SG9 = false
production-ready = false
public-online = false
drop-in replacement = false
Nakama retired = false
```

## Documentation

There is one active human documentation system. Start at [`docs/README.md`](docs/README.md).

- [`CURRENT_PLAN.md`](CURRENT_PLAN.md) — complete execution plan and closure rules
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — current/target runtime and dependency boundaries
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — development workflow and local commands
- [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) — pinned baseline, denominator and oracle
- [`docs/TESTING_AND_EVIDENCE.md`](docs/TESTING_AND_EVIDENCE.md) — test classes, CI, artifacts and review
- [`docs/SECURITY_AND_PRIVACY.md`](docs/SECURITY_AND_PRIVACY.md) — cryptography, identity, secrets and privacy
- [`docs/OPERATIONS_AND_RELEASE.md`](docs/OPERATIONS_AND_RELEASE.md) — lifecycle, databases, HA, migration and release
- [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md) — branch, merge, CODEOWNERS and administrative policy
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — critical path and team parallelization

`docs/DOCUMENTATION_AUTHORITY.json` defines the exact live Markdown allowlist. Historical/versioned human docs are removed from the active tree and remain available through Git history. Dated machine evidence is retained under `docs/evidence/` and cannot act as a current plan.

## Machine control plane

Human summaries do not override:

- `docs/status/CURRENT_STATE.json`
- `docs/status/EXECUTION_STATUS.json`
- `docs/status/GAP_REGISTER.json`
- `docs/status/IMPLEMENTATION_INVENTORY.json`
- `docs/status/PRODUCT_GATES.json`
- `docs/status/RISK_REGISTER.json`
- `docs/roadmap/NEXT_MILESTONE.json`
- `docs/evidence/index.json`
- `docs/development/PARITY_DENOMINATORS.json`

## Compatibility baseline

- Nakama `v3.40.0`
- Nakama commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`
- nakama-common `v1.47.0`
- nakama-common commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`

## Validation

Control plane and documentation:

```bash
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Root Rust workspace:

```bash
cargo fmt --all -- --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The aggregate merge gate additionally checks every registered isolated Rust workspace, the Go migration input, database profiles and path-relevant source/evidence contracts. Local success is not remote or compatibility evidence.

## Contributing and security

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for change requirements, [`SECURITY.md`](SECURITY.md) for vulnerability reporting and [`PROJECT_BOUNDARY.md`](PROJECT_BOUNDARY.md) for repository scope.

## License

Apache License 2.0. See [`NOTICE`](NOTICE) for attribution and pinned upstream metadata for exact source identities.
