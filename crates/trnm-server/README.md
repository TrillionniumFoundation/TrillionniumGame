# trnm-server

Status: **module documentation; source candidate; no compatibility, durability, SG4, production, public-online, cutover, or retirement credit**  
Path: `crates/trnm-server`  
Workspace class: `root`
Lifecycle: `canonical-server-composition-root`  
Owner role: `foundation-runtime`

This document is the module-level authority for the root-workspace Rust server composition package.

## Status and authority

`crates/trnm-server` contains the repository's only default `trnm-server` composition authority source candidate, assembled from the persistence, session/JWT, gRPC, and realtime-wire components.
`crates/trnm-persistence-pg/src/bin/trnm-server.rs` is retained only as the feature-gated `trnm-pg-compat-server` diagnostic harness and is not a second default or production authority.
The authority transfer is complete at the source/package level, but this source candidate does not by itself establish compatibility, durability, release, deployment, or production authority.

```text
compatibility_credit=false
database_durability_credit=false
sg4_credit=false
production_ready=false
public_online=false
nakama_replaced=false
```

## Responsibilities

The package parses bounded process configuration before opening listeners or database connections.
It composes the PostgreSQL repository with HTTP, generated gRPC Healthcheck, WebSocket JSON/protobuf envelopes, access-token/session operations, retry, cancellation, readiness, metrics, and drain state.
It constructs successful mutation responses only after `PgRepository` reports a durable commit or an exact prior receipt.
It enforces explicit limits for listener workers, accepted connections, request/header/body sizes, socket deadlines, WebSocket frames and messages, database pool acquisition, retry attempts, and retry elapsed time.

## Architecture and dependencies

`src/main.rs` is a thin package-local entry point and `src/runtime/` owns the candidate process modules.
`runtime/app.rs` maps typed requests to repository and session operations.
`runtime/server.rs` owns bounded listener admission and shared drain state.
`runtime/http.rs`, `runtime/grpc.rs`, and `runtime/websocket.rs` own their protocol boundaries.
`runtime/config.rs`, `runtime/pool.rs`, `runtime/retry.rs`, and `runtime/schema.rs` own validated configuration and database lifecycle policy.
The package is a member of the root workspace, uses the root lockfile, pins reviewed registry dependencies, uses path dependencies for first-party crates, and forbids unsafe code.

The package consumes:

- `trnm-contracts` and `trnm-persistence-core` for stable domain and persistence invariants;
- `trnm-persistence-pg` for PostgreSQL/Cockroach-compatible repository operations;
- `trnm-realtime-wire` for bounded realtime envelopes;
- `trnm-session-core` and `trnm-token-jwt-adapter` for session and access-token checks.

## Public contracts

The candidate CLI exposes `check-config`, `migrate`, and `serve`.
Migration uses the shared locked 0001/0002 schema runner; serve verifies existing
schema version 2, chain/catalog identity and storage writer epoch 2 without
changing metadata or schema.
The source includes:

- `GET /healthz`, `GET /readyz`, and `GET /metrics`;
- authenticated `POST /-/drain`;
- authenticated `POST /v1/authority/bootstrap` and `POST /v1/authority/commit`;
- `GET /v1/session/me`, `POST /v1/session/refresh`, and `POST /v1/session/logout`;
- session-bound `PUT /v2/storage` and `PUT /v2/storage/delete` source adapters;
- session-bound `POST /v2/storage` batch read with requested-owner ACL checks;
- session-bound `GET /v2/storage/{collection}` and `GET /v2/storage/{collection}/{user_id}` client-list projections;
- generated Nakama Healthcheck gRPC service on a separately configured listener;
- bounded WebSocket JSON and schema-bound protobuf-envelope candidate subprotocols.

These are narrow source contracts, not a claim of complete Nakama API, RTAPI, Runtime, Console, provider, IAP, social, leaderboard, tournament, matchmaker, party, or multiplayer parity.
Public errors remain typed and redact database, credential, token, and cryptographic detail.

Storage mutations decode bounded protobuf-JSON-shaped batches and preserve the
original value bytes for public MD5 versions. Owners come from a verified,
persisted candidate session principal. Calls use the bounded pool and are never
automatically retried by the generic command supervisor. The source contract is
`contracts/storage/nakama-http-storage-v1.json`. Official tokens,
source-bound historical timestamps, exact list/query/gob/collation and read query/order/multiplicity,
hooks/index, ambiguous-commit reconciliation and
exact database/oracle evidence remain open.
The database live harness includes a canonical Rust application fixture for
these routes, with required database configuration and a checked execution
marker. This does not establish TCP, SDK or immutable-oracle equivalence.

The canonical repository, pool and retry wrappers carry persisted timestamp
metadata through write, read and list responses. Fresh insert acknowledgements
require a known equal creation/update pair; Exact and changed-content receipts
require known update time. Write acknowledgements preserve database
microseconds; read/list project the same stored timestamps to whole seconds as
the pinned upstream source does. Checked `prost-types` formatting produces UTC
protobuf timestamps and rejects invalid ranges or nanoseconds as a generic 500.
Historical NULL fields remain absent: a real update can establish update time
while creation remains unknown, and a blind no-op preserves both original times.
`updated_at_ms` remains a separate explicit caller clock. The storage-only
database clock policy grants no exception for other public timestamps.

`STORAGE_LIST_ROUTES` supplies the two live GET templates. The list adapter
verifies the current principal before query and cursor parsing, then calls one
readonly serializable repository projection through the pool without generic
retry. Omitted owner lists public objects across owners; own owner lists readable
private/public objects; foreign or explicit zero owner lists public objects for
that owner. SQL applies ACL before `limit + 1`, uses text ordering and decodes only
returned rows. The sentinel supplies the next position from the last returned row.

This Nakama profile accepts the original unsigned gob/base64 cursor as an
untrusted key/UUID/read offset. It is an explicit exception to the internal typed
list's scope-bound cursor: the offset never supplies a principal or ACL authority.
The row-key projection preserves authoritative Unicode-character bounds and
permits dot/control identifiers. Collection and cursor key requests are bounded
at 4096 bytes, page limits at 100, and gob nesting/type/container work is bounded.
Unsupported gob descriptors, non-UTF8 Go strings, query alias/malformed behavior,
concurrent pagination, historical timestamp import and exact oracle/SDK qualification remain open.
The live harness requires one exact client-list repository test, its matching
profile marker and no skip, and seals its log with the existing artifact packet.
These source controls do not grant live or independent acceptance.
The same harness also requires one exact storage timestamp database test and the
isolated schema lifecycle suite, verifies the timestamp profile marker, rejects
developer skips and seals their logs with the artifact packet. Required schema,
role fencing, upgrade, recovery and compatibility evidence remain independent of
a successful local fixture.

## Operations

The source exposes liveness, readiness, redacted metrics, bounded listener workers, shared drain state, and cancellation accounting.
Non-loopback listeners require explicit opt-in.
Database transport, pool size, acquisition, statement, lock, retry, and cancellation policies are explicit and bounded.
The package must be tested with Rust 1.85.1 using:

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

Repository-wide exact-head and actual prospective-merge gates, PostgreSQL and CockroachDB live profiles, fault injection, retained artifacts, and independent specialist decisions remain separate evidence.
No production credential, deployment, traffic shift, canary, cutover, rollback-barrier removal, or retirement is authorized by this module.

## Correctness and failure model

- Command admission preserves typed validation, idempotency keys, bounded retry budgets and explicit ambiguous-commit handling.
- Accepted work is not reported as durable until the PostgreSQL transaction and acknowledgement fence have both succeeded.
- Cancellation is propagated through the bounded repository wrapper; stale or failed work does not become a success claim.
- The extracted composition root remains a source candidate until its database and protocol evidence is independently accepted.

## Security and privacy

- Database URLs, administrator tokens, session keys and client credentials are redacted from diagnostics.
- Non-loopback listeners and plaintext database transport require explicit candidate-only opt-in.
- HTTP body size, read/write deadlines, listener workers, queue depth and WebSocket message counts are bounded.
- No production credential, personal data set, custody key or protected environment secret is stored in this crate.

## Build and test

- Use Rust 1.85.1 with the root workspace `Cargo.lock`.
- Required source checks are `cargo fmt --check`, all-target tests and strict Clippy.
- The bounded process smoke verifies configuration parsing, redaction and fail-closed startup without claiming live database ingress.
- PostgreSQL and CockroachDB live lanes, protocol differentials and prospective-merge execution remain separate required evidence.

## Compatibility and evidence

- These routes are not a complete Nakama API, gRPC gateway or RTAPI implementation.
- Source-level canonical authority does not prove deployed behavior, compatibility, durability, or production acceptance.
- Compatibility, database durability, SG4, production, public-online, cutover and retirement claims require retained exact-object evidence and conflict-free specialist review.
- A green unit or smoke result alone cannot close `GAP-P0-SERVER-001`, `GAP-P0-DATA-001` or `GAP-P0-CRYPTO-001`.

## Known gaps and exit criteria

Exactly one default first-party `trnm-server` target now exists. The persistence package retains only the explicitly feature-gated `trnm-pg-compat-server` diagnostic target. A later retirement change may remove that harness only after its differential and migration use is replaced and the package authority, workflow coverage, contracts and retained evidence are updated together.
Complete official protocol and SDK differential coverage remains open.
Real KMS/HSM custody, provider/IAP, Console, multi-node routing, partition and node-loss recovery, HA, backup/PITR, capacity, rolling upgrade, and 24h/72h/7d endurance remain open.
Migration snapshot, backfill, CDC, semantic comparison, write fencing, shadow, canary, rollback, cutover, and Nakama retirement remain open.

Exit requires terminal-success exact-head and prospective-merge packets, accepted live database and protocol evidence, resolved conversations, conflict-free specialist approval, ordinary protected admission, and accepted post-merge evidence.
Until every linked criterion is satisfied:

```text
accepted_evidence=false
gap_closed=false
complete_nakama_compatibility=false
production_ready=false
public_online=false
nakama_retired=false
```

### Operator diagnostic and drain boundary

Both the canonical `runtime/app.rs` and the feature-gated diagnostic application's `App<R>` use a manual non-traversing `Debug` implementation. It emits only a redacted administrator-token field and a non-exhaustive marker; ordinary, pretty and nested formatting must not inspect repository or session internals. The implementation has no `R: Debug` requirement, preventing a future repository field from implicitly expanding this secret-bearing surface. This is output redaction, not zeroization, production secret custody or proof that every other type is redacted.

After bounded HTTP framing, the drain handler authenticates before checking its business body. Missing or wrong credentials return 401 regardless of an empty or nonempty body, without changing drain state or incrementing body-validation failures. An authenticated nonempty body returns 400 without draining. An authenticated empty body returns 200 and sets the shared drain fence. This changes the candidate operator endpoint's unauthenticated error precedence, not a Nakama compatibility claim; framing/size rejection may still happen before application authentication.

Each application path retains five native regressions: `app_debug_redacts_normal_pretty_and_nested_output`, `app_debug_does_not_require_repository_debug`, `app_debug_does_not_traverse_repository`, `drain_authentication_precedes_body_validation`, and `authenticated_invalid_drain_does_not_change_shared_state`. Synthetic credentials and a deliberately trapping repository formatter exercise the forbidden diagnostic path. Execute both targets, not only the default binary:

```bash
cargo test -p trnm-server --all-targets --locked
cargo test -p trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server --locked
```

The existing dependency pins, CLI, application route names, migration chain, session state transitions, receipt identity and token-comparison implementations are unchanged by this repair. Exact-object format/test/strict-lint, required live lanes and qualified non-author review remain necessary. Reverting either boundary would reintroduce the diagnostic leak or authentication-order regression; a source revert does not authorize a production rollback.
