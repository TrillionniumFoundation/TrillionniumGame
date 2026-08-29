# Rust server vertical slice

Status: **plan-v3 blocker implementation; source candidate only**  
Gap: `GAP-P0-SERVER-001`  
Crate: `crates/trnm-server`  
Contract: `contracts/server/rust-server-vertical-slice.v1.json`

## 1. Purpose

The repository previously contained valuable Rust state machines and database adapters but no first-party Rust process that composed them behind a bounded network lifecycle. This slice establishes that composition root before more horizontal domain work is accepted.

It does not attempt to imitate the complete Nakama wire protocol. The mutation routes use an explicitly internal fixed-width binary format so process, ownership, idempotency, queue and lifecycle invariants can be exercised without creating a false C1 claim.

## 2. Current source path

```text
ServerConfig
  -> TcpListener
  -> nonblocking accept loop
  -> bounded sync_channel<TcpStream>
  -> fixed worker pool
  -> bounded HTTP/1 parser
  -> Application
       -> /healthz
       -> /readyz
       -> /v1/bootstrap
       -> /v1/command
            -> DurableState::prepare
            -> revision/generation fence
            -> DurableState::commit
            -> receipt + event + outbox intent
```

The application state is process-local. The response therefore proves only the source composition and in-memory state-machine behavior. It is not an acknowledged durable write.

## 3. Configuration contract

Environment and CLI are intentionally small and bounded:

| Field | Environment | CLI | Bound |
|---|---|---|---|
| bind address | `TRNM_SERVER_BIND` | `--bind` | valid `SocketAddr` |
| workers | `TRNM_SERVER_WORKERS` | `--workers` | 1–64 |
| accepted queue | `TRNM_SERVER_QUEUE_CAPACITY` | `--queue-capacity` | 1–4096 |
| request bytes | `TRNM_SERVER_MAX_REQUEST_BYTES` | `--max-request-bytes` | 1 KiB–1 MiB |

Read and write timeouts are nonzero source defaults. The next configuration phase must bind every public Nakama config leaf to the D5 denominator rather than extending this list ad hoc.

## 4. Lifecycle contract

- Liveness is independent of readiness.
- Readiness becomes true only after worker startup.
- Shutdown sets readiness false and draining true before closing the accepted-work sender.
- Existing workers finish their current single request and exit when the channel drains.
- Queue saturation returns a bounded `503 request_queue_full` response rather than spawning unbounded work.
- A disconnected shutdown channel also initiates drain.

The binary currently keeps the shutdown sender alive and does not install OS signal handlers. Signal integration, multi-task supervision, hard drain deadline and forced cancellation remain mandatory follow-up work.

## 5. Internal mutation contracts

### Bootstrap

`POST /v1/bootstrap` accepts exactly 56 bytes:

```text
entity_id[16]
authority_generation:u64 big-endian
state_digest[32]
```

### Command

`POST /v1/command` accepts exactly 208 bytes:

```text
entity_id[16]
command_id[16]
fingerprint[32]
expected_revision:u64 big-endian
authority_generation:u64 big-endian
next_state_digest[32]
event_id[16]
event_payload_digest[32]
outbox_intent_id[16]
outbox_payload_digest[32]
```

The source slice supports exactly one event and one broadcast intent so the first end-to-end invariant is narrow and inspectable. It must not become the public compatibility API.

## 6. Verified-at-source invariants

Unit/source tests are required to cover:

- bounded configuration rejection;
- process health response;
- readiness before and during lifecycle;
- atomic in-memory command application;
- exact duplicate receipt replay;
- stale revision rejection;
- malformed body rejection;
- bounded server startup and drain.

Remote verification remains false until the exact PR head produces non-empty target-native Actions results.

## 7. Next integration sequence

### V1 — durable repository

Replace direct `Mutex<DurableState>` mutation with a command service and repository trait. Bind PostgreSQL and CockroachDB separately to the one authoritative migration chain. Required properties:

- transaction starts only after validation;
- bounded SERIALIZABLE retry budget;
- exact command fingerprint replay;
- receipt/event/outbox commit atomically;
- acknowledgement only after commit succeeds;
- ambiguous response replay returns the exact committed receipt;
- pool acquisition, statement and transaction deadlines;
- TLS and certificate policy;
- no external effect inside the transaction.

### V2 — durable outbox

Add a supervised worker with:

- ready-time ordering;
- bounded batch and concurrency;
- lease expiry and generation fencing;
- process/node-loss reclaim;
- exact applied receipt;
- changed receipt classified as data loss;
- atomic retry/dead-letter transition;
- drain/quarantine/reconciliation operations.

### V3 — compatibility protocol adapters

Generate adapters from reviewed D1/D2 manifests:

- HTTP JSON v2 and grpc-gateway behavior;
- native gRPC status/details;
- WebSocket JSON/protobuf envelopes;
- CID, heartbeat, close, reconnect and slow-consumer behavior;
- official SDK consumer matrix;
- immutable Nakama differential.

The internal fixed-width routes are removed or bound to a private diagnostic profile before C1.

### V4 — security and operations

Integrate:

- session/JWT verification and refresh-family revocation;
- connection/user/session revocation fanout;
- metrics, logs and traces with redaction;
- OS signal and supervised cancellation tree;
- readiness dependency graph;
- package/image/SBOM/provenance;
- backup/restore/failover and incident runbooks.

## 8. Closure criteria for `GAP-P0-SERVER-001`

The gap remains open until all of the following are true for one exact candidate:

1. the Rust binary compiles, formats and passes strict Clippy;
2. typed config, migrate, health, readiness and graceful shutdown execute;
3. one denominator-bound HTTP/gRPC route and one WebSocket JSON/protobuf route execute;
4. session verification and authority fencing are active;
5. the command commits through the production-authoritative schema;
6. receipt, events and outbox are atomic and acknowledgement follows commit;
7. response loss, restart, stale revision/generation and stale outbox worker tests pass;
8. PostgreSQL and CockroachDB have separate evidence;
9. immutable Nakama differential passes for the selected leaf set;
10. exact-head artifacts are indexed and independently reviewed.

The current source slice closes none of items 3–10 and only supplies a candidate foundation for items 1–2.
