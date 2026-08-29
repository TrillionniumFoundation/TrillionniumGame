# Rust server vertical slice alpha

Status: source candidate only. This document does not grant C1-C5, SG4, production, public-online or Nakama replacement credit.

## Purpose

The first `trnm-server` composition binary exists to stop horizontal library growth from hiding missing process-level integration. The initial binary is deliberately narrow and fail closed.

Current source path:

```text
crates/trnm-persistence-core/src/bin/trnm-server.rs
```

The binary is temporarily hosted by the persistence-core package so it enters the existing locked root workspace without creating a second package/lock authority. It must move to a dedicated `crates/trnm-server` package when the network and database adapter boundary is accepted.

## Implemented source slice

- typed commands: `serve`, `healthcheck`, and `migrate-contract`;
- bounded 64 KiB HTTP/1.1 request parser with duplicate-header rejection;
- `/healthz`, `/readyz`, and `/version` endpoints;
- one bounded command endpoint using entity revision and authority generation fences;
- exact duplicate command receipt replay;
- one event and one transactional-outbox intent in the in-memory reference state;
- deterministic HTTP error mapping for the existing stable domain codes;
- PostgreSQL and CockroachDB migration-contract inventory validation;
- source-level tests for CLI parsing, health, command apply/duplicate/fences, migration inventory and malformed requests.

Every response exposes `compatibility_credit=false` or `X-Trnm-Claim: source-candidate`.

## Explicitly not implemented

- live PostgreSQL or CockroachDB transaction repository binding;
- TLS, HTTP/2, gRPC or grpc-gateway;
- WebSocket handshake/frame processing, JSON/protobuf envelopes or reconnect lifecycle;
- JWT/session verification and socket revocation;
- bounded concurrent worker supervision, cancellation tree and signal-driven drain;
- durable outbox worker, lease expiry/reclaim and external effect adapter;
- metrics/tracing export;
- immutable Nakama oracle differential;
- official SDK compatibility;
- packaging, upgrade, HA, load, security or endurance evidence.

`GET /v1/realtime` therefore returns `426 Upgrade Required` with `websocket_adapter_not_implemented`; it must never be interpreted as realtime support.

## Required next integration slices

### VS-1 — process and configuration

- move the composition root to `crates/trnm-server`;
- bind the approved typed configuration and CLI denominator;
- add signal-driven startup, readiness, drain and shutdown state machines;
- expose structured logs and stable process exit classes.

### VS-2 — durable command

- bind `PgRepository` through a bounded pool/TLS/deadline interface;
- apply the production-authoritative migration-chain digest;
- retry only approved SERIALIZABLE failures inside a total request deadline;
- acknowledge only after commit;
- replay the exact receipt after response loss or reconnect;
- execute separately against PostgreSQL and CockroachDB.

### VS-3 — protocol adapters

- generate and bind one pinned API method through HTTP/JSON and gRPC;
- implement one pinned RTAPI request/event through WebSocket JSON and protobuf;
- preserve exact status, text, headers, CID, close reason and size limits;
- execute official SDK and immutable-oracle differentials.

### VS-4 — durable outbox and recovery

- lease with owner, generation and expiry;
- reclaim expired work after crash;
- fence stale workers;
- apply exact receipts or classify changed receipts as data loss;
- prove process kill, ambiguous commit, database reconnect and node drain.

## Acceptance boundary

The source candidate may advance `GAP-P0-SERVER-001` only after the exact PR head passes the aggregate Rust gate. The gap remains open until VS-1 through VS-4, both live database profiles, immutable differential evidence and independent protocol/database review are accepted.