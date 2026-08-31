# Nakama gRPC Healthcheck source v1

Status: **generated source candidate; not full Nakama gRPC compatibility**.

## Pinned upstream identity

The source subset is derived from:

```text
repository: heroiclabs/nakama
commit:     d4d92f93f78bbbe62c7fc50a3f85c772ec121a09
path:       apigrpc/apigrpc.proto
blob:       1cc63aae1aaa5dc56ede9c9d0b6f9a95ff91361c
package:    nakama.api
service:    Nakama
method:     Healthcheck
```

The exact upstream signature is:

```proto
rpc Healthcheck (google.protobuf.Empty) returns (google.protobuf.Empty)
```

The corresponding gRPC method path is:

```text
/nakama.api.Nakama/Healthcheck
```

## Generation boundary

`crates/trnm-persistence-pg/build.rs` compiles the checked-in narrow proto with exact package versions and a vendored `protoc` binary. No network fetch occurs during code generation.

The source candidate pins:

- tonic `0.14.5`;
- tonic-prost `0.14.5`;
- tonic-prost-build `0.14.5`;
- prost `0.14.3`;
- protoc-bin-vendored `3.2.0`;
- Rust `1.85.1` from the workspace.

The repository does not use tonic `0.14.6` because that release raises the crate's Rust baseline beyond the workspace toolchain.

## Process integration

`TRNM_SERVER_GRPC_BIND` enables the listener. It is disabled by default so existing HTTP-only deployments do not silently expose a new port.

When enabled:

- the listener runs inside the `trnm-server` process on a dedicated named thread and current-thread Tokio runtime;
- the generated `NakamaServer` serves the official Healthcheck method path over HTTP/2;
- HTTP and gRPC listener addresses must differ;
- non-loopback gRPC binding requires the same explicit public-bind opt-in as HTTP;
- gRPC startup or runtime failure marks the shared process worker-failure fence;
- authenticated HTTP drain triggers gRPC shutdown through the shared atomic drain flag;
- the main process joins the gRPC worker before reporting a clean drain.

## Source verification

Tests cover:

1. exact method path;
2. direct generated-service response;
3. generated client to generated server over HTTP/2;
4. default-disabled configuration;
5. explicit loopback configuration;
6. public-bind rejection without opt-in;
7. HTTP/gRPC address collision rejection;
8. shared drain and worker failure behavior.

## Remaining work

This subset does not implement:

- the rest of `nakama.api.Nakama`;
- grpc-gateway or the `GET /healthcheck` annotation path through generated gateway code;
- authentication interceptors for protected methods;
- request-size, concurrency and per-method deadline policy beyond the current single empty-message method;
- TLS/mTLS and certificate rotation for the gRPC listener;
- reflection, health-v1 or service discovery;
- immutable Nakama wire differential;
- official SDK black-box evidence;
- load, malformed HTTP/2, cancellation, graceful-drain, HA or endurance evidence;
- independent protocol/security acceptance.

## Claim boundary

A green source gate proves only that a generated Rust implementation of the pinned official Healthcheck signature exists and functions over a local HTTP/2 transport. `full_nakama_grpc_implemented`, `grpc_gateway_implemented`, C1/C2, SG4, production readiness, public online, replacement and retirement remain false.
