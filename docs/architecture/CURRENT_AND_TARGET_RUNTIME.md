# Current and target runtime architecture

Status: binding architecture boundary for plan v3. This document describes facts and transition rules; it does not grant compatibility or production credit.

## 1. Current runnable topology

The repository's currently runnable path is:

```text
client/operator
  -> official Nakama v3.40.0 process
       -> compiled first-party Go plugin from runtime/
       -> PostgreSQL used by Nakama and the plugin
       -> World transition compatibility fixtures where configured
```

The Rust crates under `crates/` are implementation candidates and conformance cores. They are not yet wired into a first-party server process that accepts Nakama HTTP, gRPC and realtime traffic. A successful unit test for a Rust state machine therefore proves only that state-machine contract, not a running Nakama replacement.

The Go plugin remains useful as:

- a source-migration input;
- an immutable/behavioral oracle for existing Trillionnium runtime behavior;
- a fixture producer for authoritative match, command, restart and World transition contracts;
- a rollback-safe current authority until a Rust cohort is explicitly promoted.

It must not be presented as target Rust-server evidence.

## 2. Target 1.0 topology

```text
clients and operators
  -> edge/load balancer
  -> trnm-server Rust processes
       -> HTTP/JSON gateway
       -> gRPC API
       -> WebSocket JSON/protobuf
       -> session/auth adapters
       -> domain services and Runtime hosts
       -> authoritative entity/route ownership
       -> PostgreSQL or CockroachDB profile
       -> transactional outbox and workers
       -> search/cache/provider adapters
       -> telemetry and administration plane
```

Final first-party production topology contains no Go server, Go sidecar or compiled Go plugin loader. Existing Go modules are migrated from source to an approved Rust, WASM, Lua or JavaScript profile and are tracked under `TG-PAR-055`.

## 3. Required Rust process composition

The first `trnm-server` process must provide these independently testable layers:

1. **bootstrap** — version/build identity, config load, secret references, process limits;
2. **CLI** — serve, migrate, healthcheck, config validation and diagnostics;
3. **lifecycle** — readiness, liveness, graceful drain, cancellation and bounded shutdown;
4. **protocol ingress** — HTTP/JSON, gRPC and WebSocket framing with exact limits and errors;
5. **identity/session** — token verification, refresh/revoke, connection binding and socket disconnect;
6. **authority** — one owner per entity/route, revisions and ownership generations;
7. **persistence** — one authoritative migration chain, serializable transaction and exact receipts;
8. **effects** — transactional outbox, bounded workers, lease generation and reconciliation;
9. **runtime/domain** — storage and one minimal authoritative command before broad domain expansion;
10. **observability** — request IDs, stable metrics, redaction, traces and evidence metadata.

No network adapter may bypass the domain error model or call SQL directly. No domain service may publish an external effect before its source transaction commits.

## 4. Minimal vertical request flow

The SG4 foundation request path is:

```text
receive bounded request
  -> authenticate and bind project/user/session
  -> parse exact command identity and expected revision
  -> acquire current authority generation
  -> prepare deterministic transition outside DB I/O
  -> begin SERIALIZABLE transaction
       -> compare entity revision and authority generation
       -> write next entity head
       -> write command receipt
       -> append contiguous events
       -> append ordered outbox intents
     commit
  -> acknowledge using committed receipt
  -> worker leases and applies outbox intent
  -> retry/reconnect returns the same committed receipt
```

Required failure proofs:

- malformed input changes no state;
- stale revision or generation changes no state;
- event or outbox constraint failure rolls back all prior writes;
- commit success plus response loss replays exactly one receipt and one visible effect;
- process death after commit does not lose acknowledged state;
- stale worker cannot apply after re-lease;
- shutdown stops admission, drains bounded work and leaves recoverable leases.

## 5. Migration authority by phase

| Phase | Request authority | Durable writer | Token issuer | Realtime owner | External effects |
| --- | --- | --- | --- | --- | --- |
| `nakama_primary` | Nakama | Nakama | Nakama | Nakama | Nakama |
| `rust_shadow_no_effect` | Nakama | Nakama | Nakama | Nakama | Nakama; Rust records observations only |
| `rust_canary_new_entities` | cohort router | one owner by entity cohort | one owner by session family | one owner by connection/entity | only the selected owner |
| `rust_primary_new_entities` | Rust for new entities | Rust for those entities | Rust for its session families | Rust for its routes | Rust outbox |
| `nakama_read_only` | Rust | Rust | Rust | Rust | Rust |
| `nakama_retired` | Rust | Rust | Rust | Rust | Rust |

The same session family, party, ticket, match, scheduler, IAP transaction or durable command may never have two writable owners. Shadow mode may not sign tokens, join real matchmaker pools, broadcast, settle value or write authority state.

## 6. Database authority

`migrations/` is the only production-authoritative schema chain. `database/schema/v2/` is quarantined design history. See `docs/development/SCHEMA_AUTHORITY.json`.

The server must bind the complete ordered migration digest during startup and expose it in diagnostics and evidence. An adapter compiled against a different schema ABI fails readiness. PostgreSQL and CockroachDB remain separate support profiles; one passing profile never grants the other profile credit.

## 7. Protocol ownership

Generated protocol types and public adapter behavior are isolated from domain cores:

```text
wire bytes
 -> generated/strict adapter
 -> versioned internal command/query
 -> domain service
 -> stable domain error
 -> transport-specific response/close mapping
```

This preserves the ability to reproduce Nakama's wire behavior while keeping internal hardened profiles explicit. Error text, headers, retry metadata, JSON/protobuf mapping, CIDs and socket close reasons are adapter contracts and require differential evidence.

## 8. Runtime hosting boundary

Runtime code receives a capability object, not arbitrary access to server internals. Each invocation has:

- immutable request/session context;
- explicit database/storage/social/match capabilities;
- deadline and cancellation;
- memory/fuel/CPU and output budgets;
- deterministic or recorded clock/random/provider inputs where required;
- bounded log/metric emission;
- no access to raw secrets or unrestricted network/file APIs.

JavaScript, Lua, WASM and Rust-native profiles have separate compatibility and security conclusions. A spike or engine benchmark does not close Runtime parity.

## 9. Realtime ownership boundary

Each connection has one process owner and monotonically increasing connection/route generations. Every cross-node message carries sufficient generation and session revocation information to reject stale routes. Writer queues are bounded and expose deterministic slow-consumer behavior. Reconnect cursors and missed-event recovery are versioned contracts rather than best-effort hidden behavior.

Required evidence includes node drain, process death, partition, route takeover, stale fanout, refresh-family revoke, reconnect storm and queue saturation.

## 10. What does not count as end-to-end completion

The following are useful but insufficient by themselves:

- a pure Rust core unit suite;
- successful DDL application without the production adapter;
- a relay run that targets an older commit;
- a workflow file with no workflow run;
- a Go plugin running under official Nakama;
- a generated denominator whose leaves remain unclassified;
- a local-only test result;
- a status JSON edit;
- a manually closed issue.

End-to-end credit requires exact-head execution, artifacts, schema-valid evidence and independent review.

## 11. Architecture stop conditions

Stop promotion and preserve the current authority when any of these is observed:

- duplicate writer or stale authority acceptance;
- acknowledged write loss or duplicate visible value;
- schema/adapter digest mismatch;
- unexplained identity, ACL, money, sequence, version, cursor or error divergence;
- unbounded queue, task or runtime resource;
- missing/expired security, migration or restore evidence;
- empty, skipped, cancelled or older-head required checks.
