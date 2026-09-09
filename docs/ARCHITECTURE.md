# Architecture

Status: **authoritative current documentation**  
Revision: 2026-09-03

## 1. Current runtime reality

The broadly runnable path remains:

```text
client/operator
  -> official Nakama v3.40.0
       -> first-party Go plugin under runtime/
       -> PostgreSQL
       -> compatibility fixtures where configured
```

The Go runtime is a migration input and behavior oracle, not target-production evidence. Rust contains substantial source candidates, including a database-backed HTTP/WebSocket/session/outbox slice in `crates/trnm-persistence-pg` and a smaller standalone foundation executable in `crates/trnm-server`. Neither is a complete Nakama replacement.

The two server lines have different present purposes:

- `crates/trnm-persistence-pg/src/bin/trnm-server.rs` is the current canonical database-backed integration slice and the server binary named by the machine package authority;
- `crates/trnm-server` is a dependency-bounded foundation executable used to keep process/ingress/core contracts independently buildable.

This temporary split is not the target architecture. New production behavior must not create a third composition root. Convergence must move the working vertical slice behind stable service and persistence interfaces into one `trnm-server` composition root.

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

## 5. Request context and command/query split

Every public operation becomes a bounded internal context containing request/trace identity, project, optional user/session/connection identity, node, receive time, deadline, compatibility profile and redaction policy. Unvalidated header or claim strings cannot directly become database predicates.

Commands and queries are separate:

```text
CommandHandler<C> -> Result<Committed<R>, DomainError>
QueryHandler<Q>   -> Result<R, DomainError>
```

A command success contains the receipt identity needed to reconcile ambiguous responses. A query declares its consistency and staleness requirements; cache/search cannot silently replace an authoritative read.

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

The current source includes a bounded worker and fault profiles for crash-before-publish and crash-after-publish. These candidates do not establish exactly-once external effects for all adapters and remain evidence/review scoped.

## 9. Protocol adapters

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

## 11. Runtime host

Runtime modules receive explicit capabilities rather than server internals. Every invocation has immutable context, deadline/cancellation, memory/fuel/CPU/output budgets, controlled clock/random/provider inputs, bounded logging and no unrestricted secrets/network/filesystem access.

Lua, JavaScript/TypeScript, WASM and Rust-native profiles have separate compatibility and security conclusions. A benchmark or engine spike cannot close Runtime parity.

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
