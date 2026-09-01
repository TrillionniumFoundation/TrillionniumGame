# Architecture

Status: **authoritative current documentation**  
Revision: 2026-09-01

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

The current database slice already has bounded pools, statement/lock/idle-transaction timeouts, TLS verify-full source configuration, serializable transactions and bounded jittered retries. Remaining target work includes cancellation of already-running blocking operations, certificate rotation/reload evidence, saturation/churn/failover tests, ambiguous-commit reconciliation and independently accepted performance/security review.

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
