# Architecture

Status: **authoritative current documentation**  
Revision: 2026-10-02

## 1. Current runtime reality

The broadly runnable path remains:

```text
client/operator
  -> official Nakama v3.40.0
       -> first-party Go plugin under runtime/
       -> PostgreSQL
       -> compatibility fixtures where configured
```

The Go runtime is a migration input and behavior oracle, not target-production evidence. Rust contains substantial source candidates but is not a complete Nakama replacement.

`crates/trnm-server` is now the only default `trnm-server` composition root and assembles the database-backed HTTP, generated gRPC Healthcheck, WebSocket and session slice. It is not the earlier standalone in-memory foundation executable. `crates/trnm-persistence-pg/src/bin/trnm-server.rs` remains only as the feature-gated `trnm-pg-compat-server` diagnostic and compatibility harness.

Package-level authority has converged; layer-level separation is not finished. `crates/trnm-server/src/runtime/app.rs` still defines its repository trait using concrete adapter types imported from `trnm-persistence-pg` and also consumes HTTP request/response types. `runtime/auth.rs` re-exports authentication types from that adapter. The next architecture step is to extract transport-independent service and persistence contracts, not merely move another binary. No third production composition root is permitted.

The current commands, application routes, configuration names and the narrower process-smoke boundary are specified in [`DEVELOPMENT.md`](DEVELOPMENT.md). Source-level composition does not grant durable, operational, compatibility or production acceptance.

## 2. Target topology

```text
clients and operators
  -> edge/load balancer
  -> trnm-server Rust process
       -> HTTP/JSON and grpc-gateway adapters
       -> gRPC adapter
       -> WebSocket JSON/protobuf adapter
       -> identity/session middleware
       -> command/query service core
       -> authority and route ownership
       -> runtime/domain services
       -> profile-specific persistence
       -> transactional outbox workers
       -> cache/search/provider adapters
       -> telemetry and administration plane
```

The final first-party topology contains no Go server, Go sidecar or compiled Go plugin loader.

## 3. Required layers and dependency direction

The target decomposition is responsibility-oriented:

```text
trnm-server                 process composition root
  -> config / CLI / lifecycle / observability
  -> API and RTAPI adapters
  -> service command/query interfaces
  -> identity, authority, domain and runtime capabilities
  -> persistence API
       -> PostgreSQL profile
       -> CockroachDB profile
  -> outbox delivery and reconciliation adapters
```

Dependency rules:

1. wire adapters own public paths, field mapping, status, headers and close behavior;
2. adapters call typed service commands/queries, never SQL;
3. domain/service code never imports HTTP, gRPC or WebSocket framing;
4. persistence implementations satisfy a deadline-aware profile-independent contract;
5. external effects occur only after source transaction commit through the outbox;
6. library crates do not create global mutable state or detached tasks;
7. no component can publish product claims from source presence alone.

## 4. Composition root and supervised lifecycle

The final `trnm-server` process must:

1. parse CLI without opening the database;
2. load typed configuration and resolve secret references;
3. validate compatibility/native profile combinations;
4. initialize redacted telemetry before service startup;
5. create profile-specific database pools;
6. validate migration digest and schema ABI;
7. start mandatory children under one supervisor;
8. publish readiness only after all mandatory dependencies pass;
9. stop admission and drain in a deterministic bounded order;
10. expose build, configuration, schema and source identities without secrets.

Supervised children include protocol listeners, authority/route leasing, outbox workers, schedulers, runtime hosts and telemetry exporters. Every child has inherited cancellation, bounded restart policy, startup result, shutdown deadline and stable failure classification. A mandatory child failure removes readiness and triggers drain when its approved restart budget is exhausted.

These are target obligations. In particular, the current application's non-draining `/readyz` response must not be described as a complete dependency-health or supervisor proof.

## 5. Request context and command/query split

Every public operation becomes a bounded internal context containing request/trace identity, project, optional user/session/connection identity, node, receive time, deadline, compatibility profile and redaction policy. Unvalidated header or claim strings cannot directly become database predicates.

Commands and queries are separate:

```text
CommandHandler<C> -> Result<Committed<R>, DomainError>
QueryHandler<Q>   -> Result<R, DomainError>
```

A command success contains the receipt identity needed to reconcile ambiguous responses. A query declares its consistency and staleness requirements; cache/search cannot silently replace an authoritative read.

These signatures describe the target contract, not APIs already implemented by every crate. The concrete adapter must implement the shared contract; service contracts must not import their public request/result types from the PostgreSQL implementation.

## 6. Golden transaction path

```text
receive bounded request
  -> authenticate and bind project/user/session
  -> parse stable command identity and expected revision
  -> resolve authority generation
  -> prepare deterministic transition outside database I/O
  -> begin SERIALIZABLE transaction
       -> load/lock current entity
       -> find exact prior command receipt
       -> compare revision and authority generation
       -> write next head
       -> append contiguous events
       -> append ordered outbox intents
       -> insert receipt
     commit
  -> construct acknowledgement from committed or replayed receipt
  -> worker leases and applies intents using owner/generation fencing
```

Required invariants:

- malformed input changes no state;
- stale revision or generation changes no state;
- any event/outbox/receipt constraint failure rolls back the whole command;
- commit success plus response loss returns the same receipt on retry;
- process death after commit does not lose acknowledged state;
- stale workers cannot apply after re-lease;
- no provider or network I/O occurs inside the transaction.

This generic durable-command path is not automatically the refresh-token path. Consumed-token replay intentionally revokes a family even while returning an authentication error. Its separate durable transition and response-loss protocol must be specified; a blanket rollback-on-Err service adapter is incorrect.

## 7. Persistence boundary

The persistence API must be asynchronous or otherwise cancellation-aware at its public boundary, with total request deadlines including pool acquisition, lock wait, statements and retry sleep. PostgreSQL and CockroachDB use separate implementations or explicit profile adapters and separate evidence.

The current database slice has source candidates for bounded pools, statement/lock/idle-transaction timeouts, TLS verify-full configuration, serializable transactions, jittered retries and deadline/shutdown cancellation. The four-part pool implementation remains bound by `crates/trnm-persistence-pg/src/pool.rs`; no additional server root or database schema is introduced by cancellation hardening.

### Cancellation lifecycle and physical connection retirement

A cancellation token identifies a backend connection rather than a query instance. A successful cancel transport call is not acknowledgement that PostgreSQL cancelled the intended statement. Two independent fences are therefore required in the source candidate:

1. The registry publishes the in-flight gauge while holding its registry mutex. A cancel request snapshots an entry, then uses an entry-local mutex to serialize retirement with callback dispatch. Completion removes the entry and releases the registry mutex before waiting for an already-running sender. A stale snapshot observes retirement and cannot dispatch afterwards. Network I/O never holds the global registry mutex.
2. A physical pooled connection has a shared retirement flag. Both plaintext and TLS cancellation paths set it before transport I/O, including failed sends. `RetirementManager::has_broken` returns true for a retired connection regardless of driver liveness, so r2d2 discards that lease on return. Late results also retire their lease. Healthy connections remain recyclable, and the pool itself remains usable with a replacement backend.

Waiting for local callback completion alone is insufficient: the wire cancellation may arrive later. Eviction alone is insufficient: an already-captured callback could still execute after local completion. The lifecycle mutex plus physical retirement addresses both source-level races. Cancellation counters describe requests and transport outcomes, not database rollback or commit outcomes. Ambiguous commands still require exact receipt reconciliation; cancellation does not authorize blind retry or compensation.

Compiled Rust regressions cover stale snapshots, sender/cleanup ordering without registry-lock contention, panic accounting, duplicate completion and actual r2d2 retirement. The mandatory PostgreSQL lane uses a single-connection pool, asserts a changed backend PID after each cancellation, then runs `SELECT 1`. Its shutdown scenario observes the blocking query in `pg_stat_activity` rather than inferring SQL execution from a registry count. Its deadline scenario disables the independent statement timeout inside the test callback only, isolating CancelToken behavior without changing production timeout policy.

The Python lifecycle suite is a structural regression and a finite interleaving model, not a Rust memory-model proof or live database evidence. These changes do not yet prove wall-clock bounds under stalled network/TLS transport or an arbitrary non-returning synchronous operation. Exact-head Rust compilation, both database profiles, TLS cancellation/rotation/reload, saturation/churn/failover, ambiguous-commit reconciliation and independent database/performance/security/SRE acceptance remain required. No gap, gate or compatibility claim is promoted by this source change.

`migrations/` is the only production DDL authority. `database/schema/v2/` is design history and cannot be consumed by runtime, CI, backup or release tooling.

## 8. Transactional outbox

Outbox state is explicit:

```text
pending
 -> leased(owner, generation, expires_at)
 -> applied(receipt_digest)
 -> dead_letter(reason_digest)
```

Retry, reclaim and terminal exhaustion are atomic. Every mutation repeats owner and generation predicates. Repeating an identical apply receipt is idempotent; a different receipt for an applied intent is data loss. Value-moving effects require reconciliation/quarantine rather than blind retry.

The current source includes a bounded worker and fault profiles for crash-before-publish and crash-after-publish. These candidates do not establish exactly-once external effects for all adapters and remain evidence/review scoped. An effect that may already be visible but has no authenticated receipt must not be relabelled delivered merely because an attempt expired.

## 9. Protocol adapters

The canonical process includes bounded storage source adapters for
`POST /v2/storage`, `PUT /v2/storage`, `PUT /v2/storage/delete`,
`GET /v2/storage/{collection}` and `GET /v2/storage/{collection}/{user_id}`.
They verify the existing signed
access principal and persisted session family before decoding business input,
bind every mutation owner to that principal, and invoke the serializable batch
repository through the deadline-aware pool. The generic retry supervisor
forwards these mutations once because they have no durable replay receipt.
Success is constructed only after the batch repository returns.
Reads use one bounded readonly serializable transaction under the pool deadline.
They omit missing and ACL-hidden rows before decoding, and fail the whole batch
on SQL or integrity errors. Requested owners include server/global objects;
returned objects must pass requested-key, ACL, UTF-8 and digest checks.
Client listing uses a separate `list_storage_objects_nakama` projection on the
same authoritative table. Public-all queries order by read/key/user ID, own-owner
queries by read/key, and foreign/global-owner queries by key, using SQL text
collation. ACL precedes the bounded sentinel; only returned rows are decoded.
One readonly serializable transaction completes before list success is built.
The existing typed list keeps its UTF-8 byte ordering and scope-bound cursor.

The Nakama client-list profile is an explicit cursor exception: the original
unsigned gob/base64 offset carries key, UUID and read permission without actor,
owner-filter, collection, profile or project authentication. It never supplies
authorization; every request verifies the session and repeats the current ACL in
SQL. A separate row-key factory follows the existing authoritative character
constraints and permits Unicode/dot/control text. Cursor/query byte and work
budgets remain candidate restrictions, and unsupported gob descriptors, invalid
UTF-8 Go strings and exact gateway alias behavior require independent review and
immutable differential.
The live database harness also executes a canonical Rust application fixture,
separately from its retained diagnostic process. A required profile-specific
success marker prevents optional no-database skips from becoming live results.
The same harness separately executes and retains the repository ACL/OCC rollback
fixture once per profile, including insert-only rejection and persisted times. The canonical application fixture also requires a profile-specific marker for original-string condition inputs and rollback checks.

`contracts/storage/nakama-http-storage-v1.json` records pinned source
identities, request/response boundaries and residual differences. The routes
use candidate session credentials and emit persisted storage timestamps when
known. Historical rows retain unknown timestamps as NULL and omit those fields.
Blind writes with identical value and ACLs preserve stored timestamps after
authorization, OCC and integrity checks. Exact-version writes refresh the
database update time. The append-only timestamp migration and writer barrier are
described below; their exact upstream differential remains open.
After binding the actor to the owner, an insert-only write rejects an existing
object as a version conflict regardless of that row's write ACL. Blind and exact
writes retain permission rejection before an OCC mismatch. Incoming exact
conditions use an independent `ExpectedVersion` string: no MD5 parser, case
folding, trimming or 32-character restriction is applied. Empty write conditions
are blind, `*` writes are insert-only, and every nonempty delete condition,
including `*`, is literal. Existing generated `ContentVersion` values remain
lowercase request-byte MD5. Database-native JSONB value/version separation,
historical opaque stored versions and native SQL rejection of NUL input remain
unfinished; in-memory comparison does not qualify those database semantics.
Timestamp origin/precision and bounds under independent differential, exact list/query/gob/collation behavior,
read query shape/order/multiplicity, hooks, index,
ambiguous-commit reconciliation and official token differences remain blockers.
This wiring does not grant a storage or repository-wide compatibility claim.

Adapters own:

- path, method and service selection;
- JSON/protobuf field/default/enum/integer mapping;
- headers, compression and content types;
- HTTP/gRPC error details and retry metadata;
- realtime CID, envelope, opcode and close reason;
- size, rate, heartbeat, idle and queue limits;
- public error text and internal redaction.

The current gRPC implementation represents only the pinned Nakama `Healthcheck(google.protobuf.Empty) -> google.protobuf.Empty` source slice. The current WebSocket candidate supports a bounded persistent JSON and narrow protobuf-envelope path, not the full official RTAPI denominator.

## 10. Realtime ownership

A production connection actor owns immutable connection identity, authenticated identity, route generation, revocation epoch, bounded inbound/outbound queues, heartbeat/idle deadlines, subscriptions, reconnect cursor and one writer task.

Distributed routing uses generation-fenced ownership. Stale routes and revocation epochs are rejected. Required proof includes slow consumers, node drain, process death, partition, takeover, stale fanout, session-family revoke, reconnect storm and queue saturation.

Finite admitted cardinality is a safety limit, not a capacity result. Full-state cloning, retained connection high-watermarks and quiescent namespace rollover require maximum-capacity/churn benchmarks and a rolling operational recovery design. Never evict still-needed replay fences just to free memory.

## 11. Runtime host

Runtime modules receive explicit capabilities rather than server internals. Every invocation has immutable context, deadline/cancellation, memory/fuel/CPU/output budgets, controlled clock/random/provider inputs, bounded logging and no unrestricted secrets/network/filesystem access.

Lua, JavaScript/TypeScript, WASM and Rust-native profiles have separate compatibility and security conclusions. A benchmark or engine spike cannot close Runtime parity. Engine selection, ABI/value conversion, hooks, cancellation, module reload and durable service calls require concrete per-profile design before broad implementation.

## 12. Migration ownership

Authority moves through:

```text
nakama_primary
 -> rust_shadow_no_effect
 -> rust_canary_new_entities
 -> rust_primary_new_entities
 -> nakama_read_only
 -> nakama_retired
```

The same session family, party, ticket, match, scheduler, IAP transaction or durable command never has two writable owners. Shadow does not issue real tokens, join real pools, broadcast, settle value or mutate authority.

## 13. Architecture stop conditions

Stop promotion and preserve the current authority on any duplicate writer, stale-authority acceptance, acknowledged-write loss, duplicate visible value, schema/adapter digest mismatch, unexplained identity/ACL/money/sequence/version/cursor/error divergence, unbounded resource, missing security/migration/restore evidence or non-current required check.

Architecture completion is earned only by exact-head execution, accepted evidence and independent review; this document is not implementation proof.

## 14. Next service-integration contracts

The following are implementation obligations, not claims that the adapters already exist. Complete these as bounded vertical changes with native and differential tests; preserve the existing schema and public wire behavior unless a separately reviewed migration/profile decision changes them.

| Boundary | Required design decision | Mandatory failure/recovery case |
| --- | --- | --- |
| Authentication to service | Construct trusted project/user/session/profile context only after credential verification; do not accept caller fingerprints as authenticated command identity. | Forged identity, wrong project/profile, disabled account and stale session generation cannot call a mutation. |
| Refresh to persistence | Atomically persist rotation or replay-triggered revocation; separately bind a durable operation identity for approved response-loss recovery. | Crash after commit before credential response; simultaneous refresh; consumed-token retry; Err must not discard intentional revocation. |
| Storage to wire | Internal typed cursors authenticate actor, owner filter, collection, project/profile and position. The Nakama client-list exception accepts unsigned offsets while independently revalidating the principal and SQL ACL; concurrent-page semantics remain unqualified. | Cross-scope offsets cannot grant visibility; deletion between pages, same-key owner ordering, ACL before the limit sentinel, bounded malformed gob and independent protocol/security qualification. |
| Service to repository | Shared typed commands/results and one total deadline; profile adapters own SQL, retries and receipt reconciliation. | Pool exhaustion, statement cancellation, stale authority and uncertain commit must not become fabricated success or blind retry. |
| Outbox to provider | Stable idempotency key, immutable subject/payload, exact dispatch identity, authenticated outcome query and explicit quarantine. | Provider succeeds then response is lost; expired lease; stale owner publishes; terminal attempt with unknown visible effect. |
| Revoke to realtime | Durable revocation generation drives bounded fanout and actual socket termination; restore checkpoints before accepting routes. | Node restart, delayed old-generation messages, reconnect storm, full revocation-history capacity. |
| Process to operators | Distinguish liveness from mandatory dependency readiness; signal/drain propagation reaches all listeners/workers/pools. | Stop admission before drain acknowledgement; do not hang forever on a stalled synchronous child. |

For each row retain the precise request/result types, sequence, state/error table, numerical budget, schema ownership, named test targets, upstream leaves and evidence output in the applicable existing module document. A table of intentions alone does not make the work Ready or Accepted.

## Storage timestamp schema decision

ADR NAKAMA-STORAGE-DATABASE-TIME-V2 adopts the upstream database transaction clock only for storage `create_time` and `update_time`. It is an explicit compatibility exception to the general prohibition on implicit public database clocks. Domain core values remain clock-free; persistence metadata DTOs carry normalized protobuf seconds/nanoseconds separately from storage content and ACLs. The legacy `updated_at_ms` input remains explicit and does not stand in for an upstream timestamp.

Both profiles append `0002_storage_timestamps_up.sql` to the immutable foundation. Nullable TIMESTAMPTZ columns have no defaults and receive no historical backfill. New inserts set both times from the same database transaction; an effective or exact-version update preserves creation and records transaction time. Blind unchanged value/ACL returns the locked row without an UPDATE. Receipts carry actual RETURNING values and leave the repository only after commit. HTTP write acknowledgements retain subsecond precision, while the pinned read/list projection uses seconds. Infinity, out-of-protobuf-range and malformed timestamp values fail closed.

The shared persistence migration engine embeds and verifies the complete ordered lock. The tagged chain algorithm hashes each zero-based big-endian u64 position, UTF-8 path, NUL and Git blob SHA-1 bytes. Each embedded revision records its own prefix digest, explicit writer epoch and action range. Historical identities are checked against their own revision; publication compares the complete previous metadata tuple and preserves original foundation provenance. The current reviewed chain still ends at schema v2/epoch 2; the registry does not implement native JSONB conversion or schema v3. Read-only startup checks the actual catalog and version/profile/chain/writer identity without rewriting provenance for a new binary. `trnm-schema` is an operational client of this engine; it is not another server composition root. PostgreSQL append DDL and identity publication share a transaction. CockroachDB executes declared actions and resumes only an exact permitted catalog prefix, publishing metadata after catalog convergence. These profile-specific source paths still require independent database review.

Storage reads, lists, writes, deletes and writer-epoch checks explicitly address `public` tables; public transaction time calls address `pg_catalog.now()`. The migrator rejects an effective schema namespace other than the pinned profile default before executing historical unqualified DDL. Read-only schema verification remains bound to `public` even when the connection has a different search path.

The expand phase requires exclusive storage ownership. Before upgrading an existing foundation, operators must drain the old writer and revoke storage writes available directly, by inheritance or through a reachable role. The barrier includes PostgreSQL TRUNCATE and SET ROLE access, conservatively rejects PostgreSQL role administration that could restore access, and checks CockroachDB's actual DROP permission required for TRUNCATE. The engine requires an explicit non-owner, non-admin old role and verifies that privilege barrier; this declared role does not prove an inventory or drain of all writers. A new startup version check alone cannot fence an already-connected old writer. Every v2 mutation also verifies its schema and writer epoch in the transaction. No production privilege changes are automatic. After publication, a v1 binary or a destructive column rollback is forbidden; repair must proceed forward under a restored writer barrier. Contracting the legacy clock or importing known original timestamps is separate work. Unknown creation history remains NULL and blocks complete replacement until a proven import or an independently accepted policy resolves it.
