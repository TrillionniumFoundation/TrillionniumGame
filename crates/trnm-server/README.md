# `trnm-server-foundation` source vertical slice

Status: **source candidate; no compatibility, durability, SG4 or production credit**.

Machine claim boundary:

```text
canonical_server_binary=false
compatibility_credit=false
database_durability_credit=false
sg4_credit=false
production_ready=false
```

This standalone Rust foundation-prototype binary establishes a bounded process composition root for the plan-v3 vertical-slice program. The temporary canonical `trnm-server` binary remains the database-backed target in `crates/trnm-persistence-pg`; this crate cannot receive canonical server, compatibility or production credit while both lines are being consolidated.

It currently provides:

- typed environment/CLI configuration with explicit bounds;
- fixed-size worker pool and bounded accepted-connection queue;
- request read/write deadlines and maximum body/header limits;
- liveness and readiness endpoints;
- explicit readiness/draining lifecycle in the library server;
- one fixed-width bootstrap request;
- one fixed-width authority command request using `trnm-persistence-core`;
- exact duplicate receipt replay;
- revision and authority-generation fencing inherited from the core;
- transactional in-memory event/outbox creation in the core;
- malformed request, oversized body, duplicate and stale-revision tests.

## Run

```bash
cargo run --manifest-path crates/trnm-server/Cargo.toml --locked \
  --bin trnm-server-foundation -- check-config
cargo run --manifest-path crates/trnm-server/Cargo.toml --locked \
  --bin trnm-server-foundation -- serve \
  --bind 127.0.0.1:7350 \
  --workers 4 \
  --queue-capacity 128
```

Required verification:

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

## Current wire format

The two mutation endpoints deliberately use bounded fixed-width binary request bodies. They are internal vertical-slice contracts, not Nakama API compatibility surfaces.

- `POST /v1/bootstrap`: 56 bytes = entity ID (16), authority generation (u64 big-endian), state digest (32).
- `POST /v1/command`: 208 bytes = entity ID, command ID, fingerprint, expected revision, authority generation, next-state digest, one event ID/digest and one outbox ID/digest.

Responses are bounded JSON with stable source-slice fields. `GET /healthz` is liveness. `GET /readyz` reflects the server lifecycle.

## Deliberately unresolved

The following are blockers, not implied capabilities:

- canonical server package extraction and removal of the temporary dual implementation line;
- PostgreSQL/CockroachDB repository wiring and acknowledgement-after-durable-commit;
- database retry, pool, TLS, migration and outbox worker execution;
- HTTP/JSON v2, gRPC, grpc-gateway and official SDK compatibility;
- WebSocket JSON/protobuf, heartbeat, close and reconnect behavior;
- session/JWT verification and revocation fanout;
- OS signal integration, supervised async task tree and zero-downtime drain;
- metrics/traces and evidence artifact production;
- immutable Nakama differential;
- independent protocol, database, security and SRE review.

No endpoint in this crate may be advertised as Nakama-compatible until it is replaced or wrapped by a denominator-bound adapter with exact oracle evidence.
