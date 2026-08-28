# Rust foundation alpha candidate

Status: **implementation candidate; no compatibility or production credit**

This slice starts first-party Rust product code on the W1/W3/W10 critical path. It deliberately contains no network, database, wall-clock, randomness, signing, runtime-VM or external provider capability.

## Workspace

- `trnm-contracts`: stable IDs, counters, protocol version, transport-oriented error codes and retry classes.
- `trnm-authority-core`: deterministic prepare/commit, participant sequence, global match version, exact idempotent replay and authority-generation fencing.
- `trnm-session-core`: refresh-family rotation, consumed-token replay detection, family revocation and logout/admin/reset reasons.

The crates are dependency-free and `unsafe_code` is forbidden. This is intentional: mutable authority semantics must be reviewable before they are coupled to async runtimes or persistence adapters.

## Authority invariants

1. Exact command replay returns the prior receipt without advancing state.
2. Same command ID with different fingerprint or identity is terminal conflict.
3. Participant command sequence is independent per participant.
4. Match version is globally monotonic.
5. New work requires the active authority generation.
6. Pending work cannot commit after takeover or intervening state advancement.
7. A completed receipt remains replayable after ownership takeover.

## Session invariants

1. Only the active refresh token may rotate a family.
2. Successful rotation consumes the previous token and increments generation.
3. Reuse of a consumed token revokes the entire family as compromised.
4. Unknown tokens do not mutate family state.
5. Logout/admin/reset revocation is terminal.
6. Replacement token identities cannot reuse active or consumed identities.

## Evidence boundary

The repository execution container did not provide a Rust toolchain. Local evidence therefore consists of:

- deterministic Python reference-vector execution;
- TOML and static capability checks;
- embedded Rust unit-test corpus review.

The exact-head workflow must install Rust `1.85.1` and pass `cargo fmt`, `cargo test --locked`, and strict `cargo clippy` before the PR can leave Draft. Even then, the slice proves only foundation semantics—not Nakama wire/behavior parity, database durability, HA, SG4 completion, or production readiness.

## Next adapters

- PostgreSQL/CockroachDB transactional repositories and outbox;
- HTTP/gRPC/RT protocol mappings;
- cryptographic token issuance and rotation keys;
- storage OCC and ACL core;
- connection ownership and session revocation fanout;
- model checking, fuzzing and Oracle differential vectors.
