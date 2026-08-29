# Canonical framing core

Status: **implementation candidate; no compatibility credit**

This W2 slice defines one dependency-free Rust byte contract for domain-separated protocol material. It supports null, booleans, signed 64-bit integers, UTF-8 strings, opaque bytes, arrays and bytewise-sorted objects. Floats are deliberately unrepresentable.

The frame binds:

```text
magic = TRNMCAN1
domain
protocol major/minor
typed value
```

All lengths are fixed-width big-endian. Objects reject duplicate keys and sort keys by raw UTF-8 bytes. Limits bound depth, nodes, collection width, string/byte size and total output size before any caller-visible result is returned.

This crate performs no hashing, signing, parsing, database access, networking, clock access or randomness. A later reviewed crypto adapter may hash these exact bytes. Nakama-compatible JSON/protobuf serialization and signature bytes remain Oracle work.

Current non-claims:

```text
wire_compatible = false
signature_compatible = false
sg3_complete = false
production_ready = false
public_online = false
nakama_retired = false
```
