# Rust transport error mapping core

Status: **source-level candidate**.

This crate maps one internal `DomainError` into candidate HTTP, gRPC, RTAPI, WebSocket and retry dispositions. It contains no HTTP server, gRPC framework, socket implementation, serialization library or logging adapter.

## Stable source

The gRPC numbers come from the shared canonical stable-code enum. RTAPI values are locked to `nakama-common v1.47.0` `Error.Code`. HTTP status, public messages and close behavior are explicit candidates that require immutable Nakama differentials.

## Privacy

Internal reason strings never enter `TransportMapping.public_message`. Public text is selected from the stable code and typed realtime context. Authentication, data-loss and runtime failures do not expose implementation detail.

## Realtime contexts

Typed contexts prevent a generic NotFound from accidentally becoming MatchNotFound, or an Internal error from becoming RuntimeFunctionException without the corresponding boundary. Invalid code/context pairs fail closed.

## Socket phases

Before upgrade, errors reject with an HTTP status. After upgrade, protocol corruption, authentication/policy failure, overload/unavailable, and internal/data-loss conditions have distinct candidate close actions. These actions are not claimed Nakama-compatible until socket lifecycle differentials pass.

## Remaining work

- exact HTTP/gRPC/RT response byte corpus;
- grpc-gateway and official SDK error behavior;
- JSON and protobuf realtime envelopes;
- socket handshake and close-reason differential;
- headers and retry-after policy;
- rate-limit and abuse integration;
- adapter implementation and fuzzing.

No C1, disconnect compatibility, production, or public-online claim is made.
