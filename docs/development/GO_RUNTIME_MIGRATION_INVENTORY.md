# Go runtime source migration inventory

Status: machine inventory candidate. The current Go plugin remains migration input and an oracle fixture, not the target production runtime.

Generator:

```text
python3 scripts/inventory-go-runtime.py \
  --require-registrations \
  --output run/go-runtime-migration-inventory.json
```

The inventory records:

- every non-vendored `.go` source file and SHA-256;
- initializer registration kind/name/source line;
- statically visible `nk.<Method>` module calls;
- statically visible environment keys;
- HTTP and SQL call-site counts;
- one source-manifest digest;
- required runtime migration task/parity links;
- explicit false compatibility and migration claims.

## Required classification

Every registration and module call must be bound to:

- one `DEN-RUNTIME` leaf;
- current business owner and migration owner;
- target execution profile: Rust native, WASM component, Lua compatibility, JavaScript/TypeScript compatibility or removed-by-reviewed-ADR;
- exact behavior fixtures and negative cases;
- authority/data/external-effect boundary;
- resource limits and cancellation semantics;
- migration and rollback disposition;
- evidence and independent runtime/security review.

## Static-analysis limits

The inventory is not a Go parser or call graph and can miss dynamically selected registrations, interface dispatch, aliases, generated code and indirect module calls. It must be combined with:

- Go AST/type analysis;
- runtime registration instrumentation;
- immutable/instrumented Nakama startup evidence;
- production configuration and module artifact manifests;
- representative request/hook/match/job traces;
- explicit owner attestation.

A zero regex count is never proof that a capability is absent.

## Authority boundary

The current Go module may continue to serve the existing bounded legacy/oracle role while Rust slices are developed. It must not be introduced as a new Go service or sidecar, and its successful tests do not earn target Rust Runtime parity. Go production authority is removed only after every in-scope source capability is migrated, differentially verified, cut over and the old signing/authority material is revoked.