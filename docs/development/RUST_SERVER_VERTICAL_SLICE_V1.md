# Rust server vertical slice v1

Status: **source candidate only; no compatibility or production credit**.

## Purpose

`crates/trnm-server` is the first first-party Rust binary in the replacement program. It exists to force process, ingress, authority, receipt and drain concerns into one executable boundary before additional horizontal domain cores are accepted.

It is deliberately not described as a Nakama server. The current slice uses the pure in-memory persistence core and a narrow development bearer token. It does not execute the authoritative SQL migrations and does not expose gRPC or WebSocket.

## Implemented source boundary

- `serve`, `migrate` and help command parsing;
- bounded HTTP/1.1 input with a 16 KiB request and 8 KiB body limit;
- duplicate authorization/content-length rejection and transfer-encoding rejection;
- `/healthz` and `/readyz`;
- `POST /v2/rpc/trnm_vertical_slice` behind an explicit development token;
- revision and authority-generation protected command preparation;
- one event and one transactional outbox intent in the pure durable-state core;
- exact duplicate receipt replay;
- stale-revision rejection;
- deterministic `--max-requests` drain for test execution.

## Required next integration steps

The gap remains open until the same process path additionally proves:

1. typed compatibility configuration and exact CLI behavior;
2. real PostgreSQL and CockroachDB migration execution;
3. pooled TLS database access with bounded serializable retry;
4. command, event, receipt and outbox commit through the production-authoritative migration chain;
5. acknowledgement only after commit;
6. response-loss replay after reconnect/restart;
7. HTTP/gRPC and WebSocket JSON/protobuf adapters;
8. production session verification and socket revocation;
9. outbox lease/reclaim/apply worker;
10. metrics, tracing, readiness dependencies and signal-driven drain;
11. immutable Nakama differential evidence;
12. exact-head CI artifacts and independent database/security/protocol review.

## Local source gate

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

The aggregate merge gate must execute these commands even while the crate remains a standalone workspace. A missing, skipped, cancelled or older-head result is not verification.

## Claim boundary

This slice does not earn C1, C2, SG4, production readiness, public-online approval or Nakama replacement. Its only valid advancement before remote execution is:

```text
GAP-P0-SERVER-001: open -> source-candidate
```
