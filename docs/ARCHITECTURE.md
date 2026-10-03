# Architecture

Status: **authoritative current documentation**  
Revision: 2026-10-03


The canonical App now has one selected `AuthAuthorityRuntime`: disabled, durable-family, or Nakama legacy. Server startup verifies the selected schema/catalog/import state before constructing one Legacy service or owned durable verifier, then shares the selected keys/cache across workers. The three Legacy POST routes and storage Access context are source candidates; Legacy never uses durable session-family verification/rotation/revocation. `check-config` constructs no service, pool or worker, default schema remains StorageV4/epoch4, and the AccountsV5 capture gate remains false before account SQL or listeners. HTTP credential/query/body/response Debug is redacted and unauthenticated Legacy errors install a bounded static WWW-Authenticate header. Partial startup errors use the same drain/cancellation/join cleanup as normal shutdown. Exact native HTTP, paired oracle and production lifecycle qualification remain pending.


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
bind every mutation owner to that principal, and invoke the validated batch
repository through the deadline-aware pool. The generic retry supervisor
forwards these mutations once because they have no durable replay receipt.
Success is constructed only after the batch repository returns.
Reads use one bounded readonly serializable transaction under the pool deadline.
They omit missing and ACL-hidden rows before decoding, and fail the whole batch
on SQL or integrity errors. Requested owners include server/global objects;
returned objects must pass requested-key, ACL, UTF-8 and digest checks. Batch-read
visibility is read permission 2, or permission 1 for the owning principal;
stored permission 3 is not silently folded into permission 2.
Client listing uses a separate `list_storage_objects_nakama` projection on the
same authoritative table. Public-all listing selects read >= 2, own-owner
listing selects read >= 1, and foreign/global-owner listing selects read == 2.
The modes order by read/key/user ID, read/key and key respectively, using SQL
text collation. ACL precedes the bounded sentinel; only returned rows are decoded.
One readonly serializable transaction completes before list success is built.
The existing typed list keeps its UTF-8 byte ordering and scope-bound cursor.

The Nakama client-list profile is an explicit cursor exception: the original
unsigned gob/base64 offset carries key, UUID and read permission without actor,
owner-filter, collection, profile or project authentication. It never supplies
authorization; every request verifies the session and repeats the current ACL in
SQL. A separate row-key factory follows the existing authoritative character
constraints, permits 0–128 Unicode characters including empty/dot/control
text, and preserves stored nonnegative SMALLINT permissions through 32767.
Strict new-request validation remains separate. Client write requires write
permission 1, client delete accepts any positive stored write permission, and
the server actor retains its explicit bypass. Cursor/query byte and work
budgets remain candidate restrictions, and unsupported gob descriptors, invalid
UTF-8 Go strings and exact gateway alias behavior require independent review and
immutable differential.
The live database harness also executes a canonical Rust application fixture,
separately from its retained diagnostic process. A required profile-specific
success marker prevents optional no-database skips from becoming live results.
The same harness separately executes and retains the repository ACL/OCC rollback
fixture once per profile, including insert-only rejection and persisted times. The canonical application fixture also requires a profile-specific marker for original-string condition inputs and rollback checks.
The harness identifies each execution stage. On failure, a separate diagnostic
helper prints one redacted JSON record from at most two regular logs, reading
only the last 64 KiB and retaining at most 80 lines per log. Partial first and
unterminated last lines are discarded; credentials and workflow command delimiters are escaped or
redacted. Cleanup preserves the original exit status even if diagnostics fail.
These failure records grant no execution credit and cannot replace a successful
summary, checksum seal or retained profile packet.
The migration CLI also identifies pool setup, session acquisition and schema
application failures using fixed operator fields and a closed reason allowlist.
It never publishes arbitrary domain reasons or database details. A domain error
does not retain the original SQLSTATE and reports it as unknown; a real database
error may report its validated SQLSTATE. Public protocol errors retain their
existing mapping.

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
lowercase request-byte MD5. Native JSONB is the sole persisted payload authority;
historical public versions are separate opaque strings, including empty values.
Profile-specific native SQL input rejection remains distinct from the immutable
Nakama differential; in-memory comparison cannot qualify it.
Timestamp origin/precision and bounds under independent differential, exact list/query/gob/collation behavior,
read query shape/order/multiplicity, hooks, index,
ambiguous-commit reconciliation and official token differences remain blockers.
Homogeneous Nakama HTTP write/delete batches use a separate canonical-owner policy that preserves each occurrence. The bounded Go 1.26.5 `sort.Sort` translation orders execution by collection/key/canonical owner without an ordinal tie; write ACKs retain original input positions. Only the typed policy prelocks its unique keys. The Nakama policy locks and validates the current occurrence before advancing to a later key, refreshing ACL, version and native row; a later rejection rolls back every earlier effect. Client missing/conditional deletes reject the batch; only an unconditional server delete can treat a missing row as a no-op. The internal heterogeneous batch and batch-read policies retain duplicate-key rejection. Maximum 100 occurrences, raw Runtime owner-string order, hooks/index positional effects, native SQL lock/error timing, retries and SDK/immutable-oracle qualification remain separate open boundaries. The BSD-derived sorter retains its license, both source copyright years and the exact source lock; these source bindings grant no acceptance. The narrow SomeWrite candidate binds original request bytes as native JSONB with FormatText, and Exact conditions as raw native TEXT, before per-occurrence host ACL/OCC classification. Exact acquisition and UPDATE both predicate on the literal token and server-or-write1 eligibility; an excluded row uses a nonlocking skinny fallback, and an unexpectedly eligible fallback fails closed. Any occurrences first execute an honest native fifteen-column INSERT with ON CONFLICT DO UPDATE WHERE FALSE, using the original request bytes and a native UTF8 SHA256 projection digest. One returned row proves an actual insert; only that branch produces an absent previous receipt. A zero-row conflict requires the real current prior row. PostgreSQL uses the retained conflict lock; CockroachDB takes an explicit skinny ACL FOR UPDATE lock and, after authority succeeds, reads the complete prior under FOR UPDATE because its reservation does not retain that lock. A matching token/read/write tuple preserves all fifteen old fields, including unknown provenance and microsecond times; other allowed writes use a prior-bound native upsert. The staging map supplies budgets, never prior-row authority. Validated nonempty all-Any SomeWrite batches use explicit READ COMMITTED only on PostgreSQL; mixed conditions, typed batches, deletes and every CockroachDB batch remain SERIALIZABLE. No mutation is implicitly retried. These source policies and controlled SQL prerequisites do not qualify production algorithm, source HTTP equivalence or the complete concurrency schedule. SomeWrite insert-only occurrences bypass existing-row acquisition and full-row loading, bind original JSONB bytes in a bounded native projection and execute a plain INSERT. A staged None is only insertion input, not evidence that the database key is absent. The INSERT unique failure remains a hard stop with the existing version rejection; other native failures retain their classification and the first earlier semantic rejection still wins. The finite insert-only matrix requires three cases: an Any positive wait, a committed-existing star and an uncommitted-delete star positive wait followed by holder rollback. Committed-existing completion/wait counts are PostgreSQL 1/0 and CockroachDB 0/1; the deletion case requires a real plain-INSERT waiter in both profiles. Every case retains fifteen-field rollback, no ACK and same-lease readback. The extra native projection, database-internal rendering limits, full pgx preparation/grouping, isolation/retries and concurrent winner/race behavior remain unqualified. Hidden batch SQLSTATE is not reconstructed from a public version rejection. The first host ACL rejection or Exact mismatch is remembered while real later work continues until a hard error; existing insert-only conflict, native error, DataLoss or resource rejection stops further SQL. Explicit rollback precedes the inner error return. Failed rollback remains unconfirmed and retires a pooled lease only to prevent recycling, without disabling direct repositories. Outer pool deadline/shutdown may override the inner primary error. Native Write InvalidArgument with the exact database_constraint_violation reason maps to HTTP500/code13; host validation, ACL/OCC and delete mappings retain their separate behavior. The old seven fixture markers and App13/3 remain required. The added native matrix has eleven main cases plus a separately bound legal-surrogate subvector; the same fixture binds an independently observed native-expression SQLSTATE22P02 to authenticated App HTTP500, exact fifteen-field rollback and a same-connection read; the App's hidden SQLSTATE is not reconstructed. The historical held-invalid-b observation belongs to its earlier source; the new causal case uses valid held b then invalid c before held z. Controlled official PostgreSQL HTTP observations motivated this candidate but do not qualify its execution. The two late Exact exclusions mean conditional eligibility exclusion, not absence of native waiting. The required matrix binds late_exact_wait=0/late_exact_no_wait=2 for PostgreSQL and 2/0 for CockroachDB; the sealed summary must independently contain matching integer late_exact_wait_cases and late_exact_no_wait_cases. This finite profile difference follows controlled official-source primary-index causal observations with a positive Any wait, literal single Exact and early ACL/late Exact requests; old secondary-index observations and the original rejected query classifier remain separate. It changes no production SQL or isolation policy and grants no full native lock schedule, oracle acceptance or replacement claim. In the separate literal-NUL Exact condition case, PostgreSQL's native TEXT rejection stops the tail, while CockroachDB accepts the TEXT input, preserves the first ACL rejection and waits on a later Any write. That later Any query follows the profile-specific Any reservation/acquisition path; it is not a third late Exact exclusion, so the 0/2 and 2/0 late Exact counters remain unchanged. The source observation binds the later Any's actual primary-index waiter/holder join and HTTP first ACL error; it does not reveal a hidden SQLSTATE for the first NUL condition. The fixture policy is checked in its local waits body, Exact query selector and three actual profile-aware call sites. These static guards do not prove candidate native execution. Full pgx prequeue/preparation/query-group and parameter schedule, native ON CONFLICT/insert-only lock behavior, concurrent Exact predicate races, native isolation/retries, raw Runtime owner ordering, hooks/index, SDK/immutable-oracle and independent acceptance remain open. Input/projection/response bounds do not prove database-internal JSONB memory containment. No error, identity, ACL, version, occurrence or durable effect is normalized; all full compatibility and replacement claims remain false.

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

The shared persistence migration engine embeds and verifies the complete ordered lock. The tagged chain algorithm hashes each zero-based big-endian u64 position, UTF-8 path, NUL and Git blob SHA-1 bytes. Each embedded revision records its own prefix digest, explicit writer epoch and action range. Historical identities are checked against their own revision; publication compares the complete previous metadata tuple and preserves original foundation provenance. The current source chain ends at schema 4/writer epoch 4 with four SQL files per profile and twelve authoritative tables; v1/v2/v3 prefix identities and historical SQL remain immutable. Read-only startup checks the actual catalog and version/profile/chain/writer identity without rewriting provenance for a new binary. Two schema-4 import CHECK constraints admit only the complete PostgreSQL 17.6 catalog forms observed before and after native pg_dump/pg_restore, bound to the exact profile/table/name/original descriptor. Raw catalog bytes remain distinct; changed keys, validation state, bounds or predicates still fail closed. `trnm-schema` is an operational client of this engine; it is not another server composition root. PostgreSQL append DDL and identity publication share a transaction. CockroachDB executes declared actions and resumes only an exact permitted catalog prefix, publishing metadata after catalog convergence. These profile-specific source paths still require independent database review.

Storage reads, lists, writes, deletes and writer-epoch checks explicitly address `public` tables; public transaction time calls address `pg_catalog.now()`. The migrator rejects an effective schema namespace other than the pinned profile default before executing historical unqualified DDL. Read-only schema verification remains bound to `public` even when the connection has a different search path.

The expand phase requires exclusive storage ownership. Before upgrading an existing foundation, operators must drain the old writer and revoke storage writes available directly, by inheritance or through a reachable role. The barrier includes PostgreSQL TRUNCATE, TRIGGER and SET ROLE access, conservatively rejects PostgreSQL role administration that could restore access, and checks CockroachDB's actual CREATE authority for ALTER TABLE and DROP authority for TRUNCATE. The engine requires an explicit non-owner, non-admin old role and verifies that privilege barrier; this declared role does not prove an inventory or drain of all writers. A new startup version check alone cannot fence an already-connected old writer. Every current storage read/list/mutation verifies schema 4, writer epoch 4 and import-completion admission in its transaction. No production privilege changes are automatic. After publication, a v1 binary or a destructive column rollback is forbidden; repair must proceed forward under a restored writer barrier. Contracting the legacy clock remains separate work. The source transfer path below carries actual exported storage times without fabricating unknown history. Existing NULL creation history stays NULL and still blocks complete replacement until qualified source custody or an independently accepted policy resolves it.

## Storage native JSONB schema decision

ADR NAKAMA-STORAGE-NATIVE-JSONB-V3 makes native `value_jsonb` the sole payload authority and stores `public_version` independently as native VARCHAR(32). Reads return the database UTF-8 rendering, including lawful object, array, string, number, boolean and JSON null history. New HTTP writes retain object validation. Write ACKs use MD5 of exact request bytes; native rendering is never used to reconstruct that digest. Both pinned database profiles decode byte witnesses explicitly with `pg_catalog.convert_from(value_bytes,'UTF8')::JSONB::TEXT`; casting BYTEA/BYTES directly to text produces a hex display and cannot validate the request JSON. Text parameters use explicit `TEXT::JSONB` casts. Numeric parsing/rendering belongs to the actual database, with no serde/f64 normalizer.

Known `legacy-rust-v2-bytes` and `write-request-bytes` origins retain the optional raw-byte/SHA256 witness and bind its MD5 plus native rendering. Legacy Rust bytes do not prove an original Nakama request. `nakama-export-unknown-request` has NULL witnesses and a nonzero 32-byte source-manifest digest; its opaque public version is independent of the value. Schema 3 introduced this row representation; schema 4 connects the bounded source-transfer path below. A nonzero manifest digest alone does not establish import custody. Blind same-public-version/same-ACL updates preserve unknown history even if the incoming payload differs; exact updates replace value and request witness. Known differing request fingerprints with equal MD5 fail as DataLoss.

The immutable third migration expands six nullable columns, runs a typed bounded backfill, relaxes witness nullability, contracts the four new mandatory fields and adds validated provenance/digest/history checks. Before any revision action, an entire bounded keyset scan rejects invalid SHA, binary/non-UTF8/non-JSON and profile-illegal values. PostgreSQL repeats the scan under metadata and exclusive storage locks and commits conversion plus publication atomically. CockroachDB preserves old metadata across exact DDL prefixes and one-row serializable conversion batches; resumed non-NULL tuples must match byte-exact native text, not merely JSONB equality. All profiles preserve old storage times and foundation provenance. `v2_apply_source_commit` retains the actual old v2 publisher; full old metadata comparisons include its NULL/non-NULL state. New epoch metadata publishes only after whole-data postconditions.

Requests remain bounded at 1 MiB, native values at 16 MiB, staged/read results at 32 MiB and actual escaped HTTP responses at 32 MiB. These independent candidate restrictions need qualification against the full Nakama denominator. Lawful native expansion over a budget is ResourceExhausted. The internal typed policy retains actor/ACL validation before native condition checks. The homogeneous Nakama write seam binds native JSONB and raw TEXT before host ACL/OCC classification; the finite literal-NUL profile observations above do not qualify its complete native error schedule. No schema2 writer or destructive rollback is valid after publication. Forward repair requires the same exclusive writer barrier. Source tests and local native executions do not close migration, storage, independent-review or full-replacement gates.

Native projection output guards bound returned bytes and application allocations. They observe length after database-side JSONB conversion/text materialization; they do not establish a database backend memory ceiling. Numeric expansion must be tested with bounded moderate inputs and whole-batch ResourceExhausted rollback. Database-side memory containment remains an independent open requirement.


## Storage source transfer schema decision

The fourth locked file, `0004_storage_source_import_up.sql`, publishes schema 4/writer epoch 4. It widens stored collection/key checks to 0–128 Unicode characters and retains the full nonnegative SMALLINT ACL domain. The strict internal new-key policy and HTTP request validation remain separate. Two new tables, `trnm_storage_import_jobs` and `trnm_storage_import_pages`, bring the authoritative catalog to twelve tables. The singleton metadata keeps foundation provenance and the genuine prior `v2_apply_source_commit` and `v3_apply_source_commit`; an existing v3 publisher is preserved rather than replaced by the v4 apply or later verify binary. Fresh application records the actual intermediate publishers. Historical 0001/0002/0003 bytes, their revision decisions and the original six upgrade regressions plus 41 v3 case-family observations remain part of the contract.

`PgRepository::export_storage_snapshot` reads the whole native `public.storage`, including global/private owners, opaque versions, raw keys/ACLs and every lawful JSONB shape. PostgreSQL uses one read-only repeatable-read snapshot; CockroachDB uses one read-only serializable transaction whose clock identifies that execution, not an AS OF or MVCC token. Actual columns, defaults, table kind, constraints and owner references are checked. Exported timestamp values must survive an exact native equality roundtrip. The new private packet retains exact native value bytes, source catalog and query bytes, pinned initial migration/core-storage/license bodies and actual exporter source bytes. The source hash and executing binary hash are checked against actual files; explicitly supplied commit/tree labels do not prove a clean reproducible build.

`verify_storage_export` requires independent expected manifest and receipt hashes plus producer source/binary, commit/tree and execution identity. Linux descriptor-relative reads produce bounded immutable owned bytes before target acquisition. `preflight_storage_import` checks the complete packet against native target JSONB, time/key/ACL domains, source/target collation, a dedicated empty or exact-resume target and the drained legacy-writer barrier. The importer separately rejects the declared old role's effective table or column mutation, PostgreSQL TRUNCATE/TRIGGER, CockroachDB descriptor CREATE/DROP/TRIGGER/ALL grants and reachable ownership on storage, schema metadata and both import journals. Existing noninternal triggers on those four tables are rejected; revoking a trigger grant alone does not remove an installed trigger. Public-schema and current-database ownership are also rejected; an unknown ownership or privilege catalog fails closed. Target scope is supplied independently. PostgreSQL binds native system/database identity; CockroachDB reports namespace and external scope only because physical cluster identity was not observed.

`begin_storage_import`, `apply_next_storage_import_page`, `resume_storage_import`, `verify_applied_storage_import` and `finalize_storage_import` use manifest-bound job/page journals. Each serializable page atomically inserts unknown-request-origin rows, its page receipt and a complete old-checkpoint CAS. Imported values preserve opaque versions and source times without reconstructing request MD5 or creating a second payload authority. Read-only resume verifies the exact committed prefix; finalization repeats whole-inventory native reconciliation before setting completion. Ordinary storage transactions, canonical startup/readiness and pooled business admission reject an incomplete job. No automatic retry, writer revocation, normalization, row dropping or prefix repair is provided.

The packet limit is 256 MiB, with at most 10,000 rows, 100 pages and 100 rows per page, 16 MiB per native value, 32 MiB summed native-value bytes per page, 16 MiB row metadata and a 2 MiB manifest. Transfer operations use a 300-second budget and statements at most five seconds or the remaining budget. These cooperative and pool deadlines do not prove a hard bound on every synchronous kernel call. PostgreSQL source/target keys must use the same UTF8/libc database locale and deterministic default collation; CockroachDB keys must both be uncollated. Unsupported or mismatched collation fails before registration. Native JSONB conversion/text materialization still precedes length guards, so database backend memory containment remains open.

The current transfer targets a dedicated empty database or its exact import prefix. Every import guard also rejects any existing outbox row, including pending, leased, completed and dead-letter history. Ordinary command and worker mutations share the metadata admission lock; importer registration holds its exclusive lock. PostgreSQL mutable import stages additionally rewrite the metadata tuple with identical values after authority and trigger checks, so an older SERIALIZABLE business waiter aborts with 40001. A lock alone does not refresh its snapshot. Schema identity and all recorded publishers remain unchanged; CockroachDB retains its separately tested native FOR UPDATE behavior. Expired leases do not prove an external worker has stopped, and this empty-outbox restriction does not attest production worker drain. Privileged SQL rewrites are outside that service API guarantee. Repairing timestamps in an already populated NULL-history target, and importing the corresponding accounts or other Nakama domains, remain separate obligations. The CLI and private packet remain explicit candidate plaintext operations. A `native-source-ddl-fixture` reproduces pinned storage DDL and is not a running Nakama oracle; `custodian-native-database-snapshot` still needs independent operational custody. Production issuer/signature trust, physical CockroachDB identity, maximum-capacity qualification, immutable oracle/SDK parity and independent migration/operations review remain unimplemented or unaccepted. This connected source slice does not change the full Nakama denominator or grant compatibility, production, cutover or replacement authority.

The next account schema is an isolated source frontier: locked `0005_nakama_accounts_up.sql` declares the current Nakama 3.40 `users` and `user_device` fields, defaults and constraints, including both added provider IDs and the Console device preferences/push-token fields. Schema version and storage writer authority are separate: the complete embedded source chain reaches five files, while the default canonical serve/migrate profile retains the exact schema-4 prefix, twelve tables and storage writer epoch 4. `AuthoritativeSchemaTarget::NakamaAccountsV5` is explicit and rejects before namespace reads or DDL with `schema5_native_catalog_capture_pending`; no native default, constraint or index rendering has been invented. Root must bind exact PostgreSQL and CockroachDB catalog observations, qualify the adjacent migration and retain the real schema-4 publisher before activation. An account table installed outside that path is rejected by default readiness. The storage packet, its schema-4 guard framing and import/export API do not adopt account data or schema 5. The typed account reader, bounded Device transaction and concrete server adapter are source candidates behind this closed gate. Three Legacy account/session HTTP routes and startup selection are connected in App source behind that gate; account gRPC routes, account transfer, the display-name search/index profile, schema-5 migration/backup/restoration qualification and independent acceptance remain unfinished. Existing storage and migration regressions keep their schema-4 profile; source frontier identity is not a runtime migration or compatibility claim.

The complete frontier5 source and selected runtime4 identity now have separate typed source/execution-prefix contracts retaining all five source-file proofs and the original four-file storage execution identity. This source connection does not qualify a current-HEAD live-storage, backup, retry or outbox packet. Existing execution guards, the immutable roadmap, gap scope and full compatibility denominator remain in force; schema5 activation stays closed.


The server-owned legacy auth source is separate from the active durable family/epoch profile. `LegacyAuthService` owns validated, distinct Access/Refresh keys, positive TTL policy, a UTC clock and a shared bounded blacklist; it accepts no public arbitrary-provider/clock principal factory. Its fixed legacy references have no key epoch and accept explicitly supplied public 20/27-byte defaults without weakening the existing Software provider's 32-byte minimum. The 4096-byte key ceiling and token/JSON/cache quotas are local resource policy. MAC verification covers the original encoded header and payload, then the six raw claims and Go expiry rule; UUID and blacklist checks construct a private service-branded Access principal without a durable family or SID. RawURL CR/LF and unused-pad-bit behavior, last-value exact `alg` header handling and six-field Go JSON semantics remain separate from the strict canonical parser. This MAC-first error order is not a claim about every upstream parser failure priority.

Refresh checks its own fixed key and blacklist before a typed user read, retains the original token ID/issued-at and uses the durable stored username without rerunning new-input predicates. Logout validates Access and Refresh bodies in order with their own keys and owner before one quota preflight; body validation does not consult the blacklist. RemoveAll, Ban and sweep sample the clock before acquiring the cache lock, and Ban samples before key preparation. Device input uses the source POSIX predicates and byte lengths rather than identity-core username rules. The deferred trusted account UUID source runs once only after readiness, the initial lookup and missing/create choice, before the repository's at-most-five transaction attempts; the token ID precedes issued-at only after the repository outcome. The concrete `PgLegacyAuthRepository` maps SchemaNotReady/NotFound/Banned/UsernameAlreadyExists/Internal to 14/5/7/6/13 and never adds a business retry.

`LegacyDeviceAuthError::CommittedCreation` confirms this request's newly created account commit before a later local cache/RNG/clock/issuer failure, retaining its cause and bounded cleanup diagnostic. `Unconfirmed` may include unknown native commit completion; it does not prove rollback. `UnconfirmedCleanup` fails closed on an inconsistent existing-row/cleanup outcome while retaining the diagnostic without claiming a commit. Caller replay and compensation are false for every branch. Successful token bytes, legacy Verify/Refresh/Logout results and durable session authority are unchanged by this error envelope. A committed Cockroach RELEASE cleanup failure remains diagnostic and is never retried. PostgreSQL exhaustion retains failure/no ACK instead of copying a possible source rollback-error overwrite; that exception requires separate qualification.

The account reader also checks actual public relation identity and column OID/typmod/generated/identity/collation plus RLS, policy, inheritance, rules/rewrite, partition, persistence and table access method. Separate controlled read-only PostgreSQL/CockroachDB observations and five PostgreSQL catalog negatives cover finite reader guards; they do not qualify a schema-5 migration or Device transaction, and no Cockroach negative credit is claimed. Pure Go/rustc comparisons, source/mocks and six official-server HTTP observations have different execution scopes. The retained HTTP controller has three open lifecycle/deadline findings; its finite response observations do not qualify controller cleanup or delivery bounds, a paired Rust server, SDK or instrumented transactions. Legacy HTTP/configuration/startup source is connected; actual HTTP and all account gRPC qualification remain absent. The blacklist has no eviction of live revocations; quota, poison and clock failures fail closed. Its weak-reference ticker stops and joins on final service drop; platform shutdown and broad resource containment remain open. All authentication acceptance, production and full replacement flags remain false.

The capture-gated selected Legacy source composition carries an immutable `AuthoritativeSchemaTarget` through direct repository and pool constructors and every acquired lease. Old constructors select `StorageV4`; explicit `NakamaAccountsV5` is rejected by the still-false capture gate before connection or catalog I/O. Source schema 5 has five SQL members per profile while default storage publication remains schema 4, writer epoch 4 and 12 tables. Same-transaction storage admission checks the selected target; it does not grant account readiness from a storage-only identity.

Legacy user reads and Device calls use one typed pool lease and one native call through `RetryingRepository`, which adds no generic business retry. The native account engine alone owns its existing bounded attempts. A deadline or shutdown overrides the successful reply while retaining any observed created account, existing-row result, native failure, unknown completion and cleanup facts. Confirmed creation remains confirmed even when token delivery is denied. Cancellation or unknown cleanup retires the pooled lease; it does not prove rollback, forbid direct repositories, permit replay or authorize compensation.

The bounded `legacy_http_api` source connects Device, Refresh and Logout through the single selected App authority. Its production AccountsV5 capture gate remains false, so no Legacy runtime is admitted. It preserves first-object decoding, duplicate/alias/null behavior, Go Basic base64 CR/LF and unused-bit rules, raw password bytes and session omission. Seven registered option names from the captured gateway imports fail before unknown/null handling only at message scope; variables-map keys remain arbitrary. This is a captured registration closure, not a process-wide registry census. Local JSON/query/header/response budgets, invalid UTF-8 and create-overlap boundaries remain explicit. Authentication before parsing follows AGENTS rule 11 and differs from the pinned gateway's decode-first path. Disabled is the default with no materials; an explicitly selected durable-family profile owns the `/v1/session/*` routes. Legacy has no durable-family conversion, epoch/key fallback or family operations. One Legacy service/cache is shared across workers in one process; restart persistence and multi-node revocation are unqualified. All actual HTTP, native compatibility, production and full-replacement qualifications remain false.

The explicit AccountsV5 target expects 14 tables and retains storage writer epoch 4; its closed production gate prevents this source target from authorizing publication.

The transport drain precheck and App drain admission both use the existing numeric gateway envelope for the three Legacy POST paths and their query variants: HTTP 503, code 14, "Service is draining.", without a retry field. This source repair preserves generic control-route drain behavior and still requires actual HTTP and independent review.
