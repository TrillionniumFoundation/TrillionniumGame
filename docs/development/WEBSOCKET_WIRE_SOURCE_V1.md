# WebSocket wire source v1

Status: **source candidate; not a WebSocket server or Nakama realtime claim**.

## Scope

`crates/trnm-realtime-wire` defines the first bounded RFC6455 frame boundary for the Rust realtime path. The initial profile deliberately accepts only complete, non-fragmented frames with no negotiated extensions.

Client frames must be masked. RSV bits, unsupported opcodes, non-canonical lengths, excessive payloads, invalid UTF-8 and malformed close frames fail closed.

## Encoding binding

```text
negotiated JSON     -> text frame
negotiated protobuf -> binary frame
```

An opcode mismatch is an error rather than an implicit codec fallback.

## Limits

- data payload: at most 128 KiB;
- control payload: at most 125 bytes;
- client mask: required;
- server mask: forbidden/unset;
- fragmentation: rejected in the source profile;
- compression/extensions: rejected unless a later reviewed profile implements them.

## Required next layers

This source boundary must be integrated behind:

1. strict HTTP upgrade, origin and subprotocol negotiation;
2. one connection actor per accepted socket;
3. bounded inbound and writer queues;
4. heartbeat, idle, deadline and cancellation policy;
5. JSON/protobuf RTAPI envelope parser;
6. CID/error/close behavior;
7. session/user revocation fanout;
8. reconnect cursor and missed-event recovery;
9. route ownership generation and cross-node fanout;
10. drain/takeover and stale-route fencing;
11. official SDK and immutable Nakama differential;
12. slow-consumer, reconnect-storm, node-loss and load evidence.

## Source gate

```bash
cargo fmt --manifest-path crates/trnm-realtime-wire/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-realtime-wire/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-realtime-wire/Cargo.toml --all-targets --locked -- -D warnings
```

## Claim boundary

A passing source gate proves only the bounded frame codec. Socket lifecycle compatibility, C1/C2, SG4/SG6, multi-node operation and production readiness remain false.
