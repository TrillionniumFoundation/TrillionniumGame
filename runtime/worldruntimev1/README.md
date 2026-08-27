# Nakama World runtime v1 consumer

Status: **production-language consumer core implemented; authority integration pending**

This package independently consumes and verifies unsigned
`trnm_world_runtime_v1` game-domain material inside an already authoritative
Nakama context.

It owns:

- strict JSON parsing with duplicate-key, float, exponent, signed-i64, UTF-8,
  depth, node and byte limits;
- Unicode NFC canonicalization and UTF-8 byte-order object sorting;
- World domain-separated SHA-256 reproduction;
- exact World ruleset/content selection against Nakama-owned context;
- local batch-ordinal validation without promoting it to global order;
- success and stable deterministic-error verification;
- self-validation of initial-state, command-batch, final-state, outcome and
  replay hashes;
- `trnm_world_runtime_observation_v1` construction for shadow comparison.

It explicitly does **not** own or implement:

- participant admission or authenticated roles;
- canonical global event order or command idempotency;
- restart recovery or canonical roster/event/archive roots;
- `MatchCompletedV1` construction or signing;
- Chain finality or inclusion proofs;
- CEX wallet/custody state.

## Executable consumer

```bash
go run ./cmd/world-runtime-v1-consumer \
  --input testdata/world-runtime-v1/consumer-input.json
```

The input packet binds the Nakama authority context, exact consumer revision,
World request/response and externally measured duration. Output includes a
candidate shadow observation plus explicit false authority claims.

## Current gate

The Go package and CLI are suitable for contract and shadow evidence. They are
not yet wired into authoritative Nakama match admission/order/recovery state
machines. Integration must also bind exact World and Nakama commits/trees and
run cross-repository vectors before any canonical cutover credit.
