# trnm-server

Status: **module documentation; source candidate; no compatibility, durability, SG4, production, public-online, cutover, or retirement credit**

This document is the module-level authority for the root-workspace Rust server composition package.

## Status and authority

`crates/trnm-server` contains the repository's only default `trnm-server` composition authority source candidate, assembled from the persistence, session/JWT, gRPC, and realtime-wire components.
`crates/trnm-persistence-pg/src/bin/trnm-server.rs` is retained only as the feature-gated `trnm-pg-compat-server` diagnostic harness and is not a second default or production authority.
The authority transfer is complete at the source/package level, but this source candidate does not by itself establish compatibility, durability, release, deployment, or production authority.

```text
compatibility_credit=false
database_durability_credit=false
sg4_credit=false
production_ready=false
public_online=false
nakama_replaced=false
```

## Responsibilities

The package parses bounded process configuration before opening listeners or database connections.
It composes the PostgreSQL repository with HTTP, generated gRPC Healthcheck, WebSocket JSON/protobuf envelopes, access-token/session operations, retry, cancellation, readiness, metrics, and drain state.
It constructs successful mutation responses only after `PgRepository` reports a durable commit or an exact prior receipt.
It enforces explicit limits for listener workers, accepted connections, request/header/body sizes, socket deadlines, WebSocket frames and messages, database pool acquisition, retry attempts, and retry elapsed time.

## Architecture and dependencies

`src/main.rs` is a thin package-local entry point and `src/runtime/` owns the candidate process modules.
`runtime/app.rs` maps typed requests to repository and session operations.
`runtime/server.rs` owns bounded listener admission and shared drain state.
`runtime/http.rs`, `runtime/grpc.rs`, and `runtime/websocket.rs` own their protocol boundaries.
`runtime/config.rs`, `runtime/pool.rs`, `runtime/retry.rs`, and `runtime/schema.rs` own validated configuration and database lifecycle policy.
The package is a member of the root workspace, uses the root lockfile, pins reviewed registry dependencies, uses path dependencies for first-party crates, and forbids unsafe code.

The package consumes:

- `trnm-contracts` and `trnm-persistence-core` for stable domain and persistence invariants;
- `trnm-persistence-pg` for PostgreSQL/Cockroach-compatible repository operations;
- `trnm-realtime-wire` for bounded realtime envelopes;
- `trnm-session-core` and `trnm-token-jwt-adapter` for session and access-token checks.

## Public contracts

The candidate CLI exposes `check-config`, `migrate`, and `serve`.
The source includes:

- `GET /healthz`, `GET /readyz`, and `GET /metrics`;
- authenticated `POST /-/drain`;
- authenticated `POST /v1/authority/bootstrap` and `POST /v1/authority/commit`;
- `GET /v1/session/me`, `POST /v1/session/refresh`, and `POST /v1/session/logout`;
- generated Nakama Healthcheck gRPC service on a separately configured listener;
- bounded WebSocket JSON and schema-bound protobuf-envelope candidate subprotocols.

These are narrow source contracts, not a claim of complete Nakama API, RTAPI, Runtime, Console, provider, IAP, social, leaderboard, tournament, matchmaker, party, or multiplayer parity.
Public errors remain typed and redact database, credential, token, and cryptographic detail.

## Operations

The source exposes liveness, readiness, redacted metrics, bounded listener workers, shared drain state, and cancellation accounting.
Non-loopback listeners require explicit opt-in.
Database transport, pool size, acquisition, statement, lock, retry, and cancellation policies are explicit and bounded.
The package must be tested with Rust 1.85.1 using:

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

Repository-wide exact-head and actual prospective-merge gates, PostgreSQL and CockroachDB live profiles, fault injection, retained artifacts, and independent specialist decisions remain separate evidence.
No production credential, deployment, traffic shift, canary, cutover, rollback-barrier removal, or retirement is authorized by this module.

## Correctness and failure model

- Command admission preserves typed validation, idempotency keys, bounded retry budgets and explicit ambiguous-commit handling.
- Accepted work is not reported as durable until the PostgreSQL transaction and acknowledgement fence have both succeeded.
- Cancellation is propagated through the bounded repository wrapper; stale or failed work does not become a success claim.
- The extracted composition root remains a source candidate until its database and protocol evidence is independently accepted.

## Security and privacy

- Database URLs, administrator tokens, session keys and client credentials are redacted from diagnostics.
- Non-loopback listeners and plaintext database transport require explicit candidate-only opt-in.
- HTTP body size, read/write deadlines, listener workers, queue depth and WebSocket message counts are bounded.
- No production credential, personal data set, custody key or protected environment secret is stored in this crate.

## Build and test

- Use Rust 1.85.1 with the root workspace `Cargo.lock`.
- Required source checks are `cargo fmt --check`, all-target tests and strict Clippy.
- The bounded process smoke verifies configuration parsing, redaction and fail-closed startup without claiming live database ingress.
- PostgreSQL and CockroachDB live lanes, protocol differentials and prospective-merge execution remain separate required evidence.

## Compatibility and evidence

- These routes are not a complete Nakama API, gRPC gateway or RTAPI implementation.
- Source-level canonical authority does not prove deployed behavior, compatibility, durability, or production acceptance.
- Compatibility, database durability, SG4, production, public-online, cutover and retirement claims require retained exact-object evidence and conflict-free specialist review.
- A green unit or smoke result alone cannot close `GAP-P0-SERVER-001`, `GAP-P0-DATA-001` or `GAP-P0-CRYPTO-001`.

## Known gaps and exit criteria

Exactly one default first-party `trnm-server` target now exists. The persistence package retains only the explicitly feature-gated `trnm-pg-compat-server` diagnostic target. A later retirement change may remove that harness only after its differential and migration use is replaced and the package authority, workflow coverage, contracts and retained evidence are updated together.
Complete official protocol and SDK differential coverage remains open.
Real KMS/HSM custody, provider/IAP, Console, multi-node routing, partition and node-loss recovery, HA, backup/PITR, capacity, rolling upgrade, and 24h/72h/7d endurance remain open.
Migration snapshot, backfill, CDC, semantic comparison, write fencing, shadow, canary, rollback, cutover, and Nakama retirement remain open.

Exit requires terminal-success exact-head and prospective-merge packets, accepted live database and protocol evidence, resolved conversations, conflict-free specialist approval, ordinary protected admission, and accepted post-merge evidence.
Until every linked criterion is satisfied:

```text
accepted_evidence=false
gap_closed=false
complete_nakama_compatibility=false
production_ready=false
public_online=false
nakama_retired=false
```
