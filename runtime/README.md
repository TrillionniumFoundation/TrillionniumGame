# Runtime modules

Nakama runtime implementation belongs here. Do not migrate mixed World
game-server code wholesale; extract one owned capability at a time behind a
versioned contract, invariant-focused tests and an explicit rollback boundary.

## Authoritative runtime

The Go module in this directory implements the bounded P0 authoritative match:

- signed admission and participant identity binding;
- canonical match version and event sequence;
- command idempotency and anti-replay;
- authenticated snapshots and restart/resume;
- archive catch-up and evidence roots;
- Nakama-signed `MatchCompletedV1`;
- server-owned storage and fail-closed optimistic concurrency.

Run `bash scripts/check-nakama-p0.sh` for the complete repository acceptance
gate. Passing it is repository evidence only, not cross-repository release or
public-online approval.

## World transition consumer

`world_transition_v1/` is an independent standard-library Python consumer for
the pinned World deterministic transition contract. It:

- prepares a World request from an already-authoritative Nakama context;
- derives opaque retry-stable transition and command IDs;
- verifies exact accepted/rejected World bytes and hashes;
- rejects authority-field smuggling;
- emits and compares shadow observations.

The consumer is an oracle and conformance surface. Live execution requires a
production-language adapter plus prepare → execute → verify → stale-fenced
commit orchestration. It must not execute World under an authoritative mutex or
storage transaction, and it must not permit fallback to a World-local public
authority.
