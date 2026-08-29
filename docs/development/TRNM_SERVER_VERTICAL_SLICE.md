# `trnm-server` Rust vertical slice

Status: **source candidate only**. This document grants no SG4, compatibility, durability, production, public-online, or replacement credit.

## Purpose

The repository now has a first-party Rust binary path connecting one bounded request to the existing PG-wire transaction adapter:

```text
config -> explicit migrate or schema verification -> HTTP request
       -> strict decode -> entity bootstrap or command commit
       -> receipt + event + outbox transaction -> response after commit
       -> authenticated drain
```

The target is temporarily located under `crates/trnm-persistence-pg/src/bin/` to avoid changing the reviewed dependency and lock boundary. A later reviewed change may extract a dedicated composition-root crate.

## Commands

Exactly one command is required:

```text
trnm-server check-config
trnm-server migrate
trnm-server serve
```

`serve` never auto-migrates. It opens only after the authoritative ten-table inventory and schema metadata match the selected profile.

## Configuration contract

Required settings select the database URL, the `postgresql` or `cockroachdb` profile, the exact migration source commit, and a separate drain credential. The current adapter uses `NoTls`, so plaintext database transport requires an explicit candidate-only acknowledgement. The listener defaults to `127.0.0.1:7350`; a non-loopback bind also requires explicit acknowledgement.

Request bytes, read timeout, and write timeout have hard minimum and maximum values. Debug output redacts both the database URL and drain credential.

## HTTP profile

This source candidate accepts one HTTP/1.1 request per connection and returns `Connection: close`. It requires exact `Content-Length` for POST and rejects:

- transfer encoding;
- duplicate headers;
- pipelining;
- oversized or incomplete headers/bodies;
- noncanonical request lines;
- nested, escaped, duplicate-field, or noncanonical-number JSON;
- identifiers or digests that are not fixed-width lowercase hexadecimal.

Unsupported framing fails before the repository is invoked.

## Endpoints

```text
GET  /healthz
GET  /readyz
GET  /metrics
POST /-/drain
POST /v1/authority/bootstrap
POST /v1/authority/commit
```

The drain endpoint requires the configured independent credential, accepts an empty body only, changes readiness to false, rejects new mutations, finishes the current sequential request, and exits the listener loop.

The bootstrap operation receives an entity ID, authority generation, state digest, and update time. The commit operation receives one command identity and fingerprint, expected revision and generation, next state, one event, and one outbox intent. Intent kinds are `broadcast`, `search_index`, `notification`, `external_effect`, and `completion`.

## Acknowledgement fence

The application constructs the commit response only after `PgRepository::commit_command` has either:

1. committed the SERIALIZABLE head/receipt/event/outbox transaction; or
2. loaded the exact receipt for the same entity, command, and fingerprint.

A response-delivery failure after commit does not compensate or replay the durable effect. A retry with the same command identity receives the exact duplicate receipt. A changed fingerprint remains a terminal conflict.

## Error and secret boundary

Public responses expose only a stable code, generic message, and retry class. SQL text, database URL, internal domain reason, and credentials are excluded. Process database errors expose at most SQLSTATE.

## Required source and execution tests

The candidate contains tests for canonical hex, strict JSON, bounded configuration, HTTP framing rejection, both authoritative schema profiles, in-process health/readiness/bootstrap/commit, error redaction, authenticated drain, and length-difference-safe credential comparison.

The aggregate gate must run:

```text
cargo fmt --all -- --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
python3 scripts/check-trnm-server.py
```

A workflow definition or local source inspection is not remote evidence. Credit requires a non-empty successful run for the exact PR head.

## Remaining blockers

This slice does not implement or prove:

- pool, TLS/mTLS, cancellation, or bounded total-deadline retry;
- concurrent supervised connections;
- session JWT and refresh/socket revocation;
- gRPC, grpc-gateway, or WebSocket JSON/protobuf;
- distributed routes, presence, and fanout;
- outbox delivery and reconciliation workers;
- immutable Nakama wire/database differential;
- official SDK, load, HA, endurance, penetration, or independent review evidence.

`GAP-P0-SERVER-001` therefore remains open until every close criterion and required independent evidence is accepted.
