# Rust server foundation source slice

Status: **source candidate only; no compatibility or production credit**.

## Purpose

`crates/trnm-server` is a standalone, dependency-bounded Rust foundation executable. It keeps a small process, ingress, authority, receipt and drain boundary continuously buildable while the canonical database-backed server under `crates/trnm-persistence-pg` advances.

It is not a Nakama-compatible server and is not the production runtime authority. The current slice uses the pure in-memory persistence core, does not execute the authoritative SQL migrations, and does not expose the production HTTP/gRPC/WebSocket compatibility surfaces.

## Current implemented source boundary

- `serve`, `check-config`, `version` and help command parsing;
- typed environment and CLI configuration for bind address, worker count, queue capacity and request-size limit;
- `TRNM_SERVER_MAX_REQUEST_BYTES` plus `--max-request-bytes`, validated from 1 KiB through 1 MiB with a 256 KiB default;
- bounded HTTP/1.0 and HTTP/1.1 parsing with a 16 KiB header ceiling;
- duplicate `Content-Length` rejection and unconditional `Transfer-Encoding` rejection;
- exact 56-byte bootstrap and 208-byte command bodies;
- `/healthz` and `/readyz`;
- `POST /v1/bootstrap`;
- `POST /v1/command`;
- revision and authority-generation protected command preparation;
- one event and one transactional outbox intent in the pure durable-state core;
- exact duplicate receipt replay;
- stale-revision rejection;
- a bounded synchronous worker queue, bounded worker count and socket read/write timeouts.

## Exact process smoke

`scripts/check-rust-server-process.sh` builds `trnm-server-foundation`, launches it on a loopback address, and exercises:

1. health;
2. readiness;
3. bootstrap;
4. an applied command;
5. exact duplicate receipt replay.

The process smoke deliberately terminates the prototype explicitly after the bounded ingress checks. It records:

```text
process_ingress_verified=true
graceful_shutdown_verified=false
database_durability_verified=false
compatibility_credit=false
production_ready=false
```

The fail-closed result therefore keeps `graceful_shutdown_verified=false` explicit. A successful process smoke proves only that this standalone foundation executable starts and serves its narrow source contract; it does not prove a signal-driven graceful shutdown or durable database behavior.

## Required next integration steps

The gap remains open until the canonical database-backed process path additionally proves:

1. exact CLI and production configuration behavior against the pinned denominator;
2. real PostgreSQL and CockroachDB migration execution;
3. pooled TLS database access with bounded serializable retry and cancellation;
4. command, event, receipt and outbox commit through the production-authoritative migration chain;
5. acknowledgement only after commit;
6. response-loss replay after reconnect and restart;
7. official HTTP/gRPC and WebSocket JSON/protobuf adapters;
8. production session verification and socket revocation;
9. outbox lease/reclaim/apply reconciliation;
10. metrics, tracing, dependency readiness and signal-driven drain;
11. immutable Nakama differential evidence;
12. exact-head CI artifacts and independent database, security and protocol review.

## Required gates

```bash
python3 scripts/check-rust-server-source-candidate.py
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
bash scripts/check-rust-server-process.sh
```

The aggregate merge gate must execute the build, test, lint and process checks even while the crate remains a standalone workspace. A missing, skipped, cancelled, empty or older-head result is not verification.

## Claim boundary

This slice does not earn C1, C2, SG4, production readiness, public-online approval or Nakama replacement. Its valid state remains bounded to a remotely executed source candidate until the database-backed vertical slice and required independent reviews are accepted.
