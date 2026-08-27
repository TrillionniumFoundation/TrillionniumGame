# Runtime modules

Nakama runtime implementation belongs here. Do not migrate mixed World
game-server code wholesale; extract one owned capability at a time behind a
versioned contract and invariant-focused tests.

## Current module

`world_transition_v1/` is an independent standard-library reference adapter for
the pinned World deterministic transition contract. It:

- prepares a World request from an already-authoritative Nakama context;
- derives opaque retry-stable transition and command IDs;
- verifies exact accepted/rejected World bytes and hashes;
- rejects authority-field smuggling;
- emits and compares shadow observations.

It does not yet implement the deployed Nakama runtime-language module,
persistent ordering/idempotency/recovery, canonical roots or completion signing.
