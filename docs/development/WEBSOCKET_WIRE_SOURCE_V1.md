# WebSocket wire source v1

Status: **bounded persistent source candidate; not a Nakama realtime compatibility claim**.

## Scope

`crates/trnm-realtime-wire` is now the shared strict RFC 6455 frame boundary used by the first-party `trnm-server` WebSocket path. The source profile accepts complete, non-fragmented frames with no negotiated extensions and enforces a maximum 128 KiB payload.

The server performs the HTTP upgrade, chooses the first supported client-offered subprotocol, and keeps the connection open for a bounded sequence of frames instead of closing after one authority command.

## Subprotocols

```text
trnm.json.v1     -> text frame carrying the existing strict JSON authority request/response
trnm.protobuf.v1 -> binary frame carrying trnm-authority-envelope-v1.proto
```

The protobuf envelope is intentionally narrow:

```proto
message AuthorityCommandEnvelope { bytes json_request = 1; }
message AuthorityResponseEnvelope { uint32 status = 1; bytes json_body = 2; }
```

It supplies a deterministic binary transport into the same post-commit application path. It is **not** the official Nakama realtime protobuf schema, and it does not grant protobuf or realtime compatibility credit. Unknown fields, duplicate fields, non-minimal varints, invalid UTF-8 JSON, invalid status values and excessive lengths fail closed.

## Persistent lifecycle

- at most 64 received messages per accepted connection;
- client data opcode must match the selected JSON or envelope encoding;
- ping is answered with pong carrying identical bytes;
- pong is accepted without application dispatch;
- valid close is echoed and terminates the connection;
- malformed framing closes with protocol error;
- invalid encoding closes with unsupported-data or invalid-data status;
- read timeout converges through going-away close when the socket remains writable;
- drain after an application response closes the upgraded connection;
- every accepted data message reaches the same authority application path and therefore retains the existing acknowledgement-after-commit fence.

## Limits

- data payload: at most 128 KiB;
- protobuf JSON body: at most 128 KiB minus envelope overhead;
- control payload: at most 125 bytes;
- client mask: required;
- server mask: forbidden/unset;
- fragmentation: rejected;
- compression/extensions: rejected;
- connection message budget: 64.

## Remaining layers

The following remain explicitly open:

1. official Nakama realtime protobuf messages and generated standard-runtime bindings;
2. gRPC and grpc-gateway;
3. asynchronous bounded writer queues and slow-consumer eviction;
4. server-initiated heartbeat scheduling;
5. persistent session expiry and revocation fanout;
6. reconnect cursor and missed-event recovery;
7. route ownership generation and cross-node fanout;
8. drain/takeover and stale-route fencing across nodes;
9. official SDK and immutable Nakama differential;
10. reconnect-storm, node-loss, saturation, load and endurance evidence;
11. independent protocol and security acceptance.

## Source gates

```bash
cargo fmt --manifest-path crates/trnm-realtime-wire/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-realtime-wire/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-realtime-wire/Cargo.toml --all-targets --locked -- -D warnings
cargo test --package trnm-persistence-pg --bin trnm-server --locked
cargo clippy --package trnm-persistence-pg --bin trnm-server --locked -- -D warnings
```

## Claim boundary

A passing source gate proves a bounded shared frame codec, a narrow schema-bound binary envelope and a bounded persistent connection loop. Official Nakama protobuf/gRPC behavior, C1/C2, SG4/SG6, multi-node operation, production readiness, public online, replacement and retirement remain false until exact-target differential evidence and independent acceptance exist.
