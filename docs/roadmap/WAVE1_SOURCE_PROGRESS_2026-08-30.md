# Wave 1 source progress — 2026-08-30

Status: source progress only. Exact-head remote execution and independent review remain required.

## Added in the active plan-v3 line

### First Rust process candidate

`crates/trnm-server` now provides a bounded first-party Rust executable boundary with:

- typed `serve`, `migrate` and help commands;
- health/readiness;
- bounded HTTP parsing and transfer-encoding rejection;
- explicit development bearer-token boundary;
- one revision/generation-protected command;
- event and outbox creation in the pure durable-state core;
- exact duplicate receipt replay;
- stale revision failure;
- deterministic drain after a bounded request count.

This advances `GAP-P0-SERVER-001` only to a source candidate. Live database, HTTP/gRPC compatibility, WebSocket, production session verification, outbox delivery, signal drain and oracle evidence remain open.

### Storage public version candidate

`crates/trnm-storage-nakama-version` now separates:

- the public lowercase MD5 storage version;
- the client `Blind`, `CreateOnly` and `Exact` write condition;
- a separate 32-byte internal integrity digest type.

RFC 1321 and OCC source tests are present. Integration into `trnm-storage-core`, both database profiles and the immutable oracle remains open.

### Candidate identity envelope

`scripts/generate-candidate-manifest.py` and `candidate-identity-manifest.yml` bind:

- exact commit/tree;
- migration-chain digests;
- plan/status/gap/gate/evidence digests;
- server, storage-version, JWT and persistence source digests.

The identity envelope deliberately starts with validation/review/compatibility claims set to false.

## Already fixed at source level in the same line

- full-width JWT comparison length difference plus a 256-byte regression;
- atomic outbox attempt-limit dead-letter transition;
- required-live database environment contract;
- canonical post-rename Go module path;
- one production schema authority and quarantine of the alternate design family;
- stale integration PR closure without history deletion;
- plan-v3 status/gap/evidence/gate control plane;
- stable aggregate merge-gate source definition.

## Mandatory next evidence

1. exact current-head native workflow/check collection;
2. formatter, test and strict Clippy result for every Rust workspace;
3. control-plane and Go migration-input result;
4. current-head PostgreSQL and CockroachDB execution;
5. independent database, security, protocol and realtime reviewers;
6. artifact digests entered into the evidence index;
7. refreshed gap states derived from accepted evidence.

Nothing in this progress record earns C1-C5, SG0-SG9, production readiness, public-online approval or Nakama replacement.
