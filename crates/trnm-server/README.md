# `trnm-server` canonical composition root

Status: **source candidate; no compatibility, durability, SG4 or production credit**  
Path: `crates/trnm-server`  
Workspace class: `isolated mandatory aggregate target`  
Owner role: `foundation-runtime`

## Purpose

This package is the single first-party Rust server process composition root. It owns typed process configuration, migration invocation, HTTP, the bounded Nakama Healthcheck gRPC subset, persistent WebSocket handling, session endpoints, graceful drain and the connection-worker lifecycle. The persistence implementation remains in `trnm-persistence-pg`; the executable process no longer belongs to the persistence package.

The source modules under `src/runtime/` are moved byte-for-byte from the previously exercised temporary `trnm-persistence-pg/src/bin/trnm_server/` tree except for path-sensitive composition files. The move deliberately does not widen protocol, authority or production claims.

## Commands

The binary has no implicit serve mode. Exactly one command is required:

```bash
cargo run --manifest-path crates/trnm-server/Cargo.toml --locked --bin trnm-server -- check-config
cargo run --manifest-path crates/trnm-server/Cargo.toml --locked --bin trnm-server -- migrate
cargo run --manifest-path crates/trnm-server/Cargo.toml --locked --bin trnm-server -- serve
```

`check-config` parses and redacts the complete typed configuration. `migrate` applies only the authoritative migration chain and verifies its required table set. `serve` verifies schema metadata before binding the service.

## Architecture

```text
trnm-server process root
  -> typed configuration and fail-closed startup
  -> trnm-persistence-pg pool/repository/TLS/cancellation
  -> authoritative migrations/
  -> HTTP authority/session handlers
  -> bounded gRPC healthcheck subset
  -> strict WebSocket framing and authority envelopes
  -> shared drain, worker failure fence and bounded shutdown
```

The package contains one binary target, `trnm-server`. The former `trnm-server-foundation` prototype is superseded by this composition root; a later cleanup increment must remove the old persistence-package auto-discovered server entry after all scripts, machine status and workflow contracts point to this target.

## Configuration and fail-closed defaults

The runtime preserves the existing typed `TRNM_SERVER_*` contract. Loopback binding and plaintext database use require their existing explicit candidate controls. Database URLs, administrative tokens, session key material and private-key paths are redacted from debug output. Invalid/missing profile, TLS pairing, pool bounds, request limits or command values terminate startup.

## Correctness and failure model

- the authoritative schema is verified before serving;
- repository operations use bounded pool acquisition, statement/lock/idle transaction limits and total retry budgets;
- ambiguous database completion is reconciled through exact command identity and receipt replay;
- WebSocket frames, envelopes, messages per connection and worker queues are bounded;
- shared drain fences already-upgraded sockets as well as new accepts;
- a connection or gRPC worker panic begins global drain;
- shutdown requests in-flight database cancellation and joins all owned workers;
- source tests preserve existing response-loss, restart, saturation, malformed-frame and session replay behavior.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

The exact standalone lockfile, vendored `protoc`, narrow proto source, source commit/tree, generated code and all runtime module blobs must be bound in retained evidence. Empty, skipped, cancelled, older-head or stale-base execution receives no credit.

## Security and privacy

The process holds only the scoped material required by its configured adapters. It must not log database credentials, JWT keys, refresh secrets, administrative tokens, private-key bytes, raw user payloads or provider credentials. Production key custody, certificate issuance, revocation operations and provider authority remain external gates.

## Compatibility and evidence boundary

This package preserves only the already declared vertical-slice behavior. It does not establish complete Nakama API/RTAPI/Runtime/Console/provider/IAP compatibility. The narrow gRPC service is Healthcheck only; the WebSocket protobuf envelope is a bounded authority subset, not the complete Nakama realtime denominator.

Promotion requires exact-head and prospective-merge execution against both database profiles, retained source/build/artifact identities, and independent protocol, database, security and SRE acceptance. HA, PITR, capacity/endurance, multi-node routing, canary, cutover, public online and Nakama retirement remain false until their separate evidence gates pass.
