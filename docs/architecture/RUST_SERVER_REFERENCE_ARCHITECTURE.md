# Rust server reference architecture

Status: design contract for the first `trnm-server` vertical slice and subsequent domain expansion. It is not an implementation or compatibility claim.

## 1. Design goals

The server architecture must simultaneously support:

- exact declared Nakama compatibility profiles;
- one-writer authority and stale-owner fencing;
- acknowledged-write durability and exact idempotent replay;
- bounded memory, queues, tasks and runtime resources;
- explicit deadlines, cancellation and graceful drain;
- PostgreSQL and CockroachDB as separate evidence profiles;
- differential testing against immutable and instrumented oracles;
- staged migration without synchronous dual writes;
- operational diagnosis without leaking secrets or personal data.

Correctness, identity, permission, money and durability are never traded for lower latency.

## 2. Workspace shape

Target workspace additions are organized by stable responsibility rather than by transport handler:

```text
crates/
  trnm-server/                 binary bootstrap and composition root
  trnm-config/                 typed config, precedence and diagnostics
  trnm-cli/                    serve/migrate/healthcheck/config commands
  trnm-api-generated/          pinned generated public protocol types
  trnm-api-adapter/            HTTP/gRPC/grpc-gateway mapping
  trnm-rtapi-adapter/          WebSocket JSON/protobuf mapping
  trnm-service-core/           command/query dispatch and request context
  trnm-persistence-api/        async repository traits and transaction contracts
  trnm-persistence-pg/         PostgreSQL profile
  trnm-persistence-crdb/       CockroachDB profile or explicit profile adapter
  trnm-outbox-worker/          leases, retries, receipts and reconciliation
  trnm-runtime-host/           capability-limited runtime invocation
  trnm-observability/          metrics, traces, logs and evidence identity
```

Existing pure cores remain reusable where their contracts match. A new crate requires an owner, dependency reason, public boundary, resource model, tests and claim mapping; crate count is not a progress metric.

## 3. Composition root

`trnm-server` is the only production composition root. It:

1. parses CLI without accessing the database;
2. loads typed config and resolves secret references;
3. validates compatibility/native profile combinations;
4. initializes telemetry with redaction before service startup;
5. creates database pools with profile-specific configuration;
6. validates migration digest and schema ABI;
7. starts ownership, outbox, provider and protocol services under a supervised task tree;
8. flips readiness only after mandatory dependencies and schema checks pass;
9. handles drain/cancellation in a deterministic order;
10. records build, config, schema and source identities in diagnostics/evidence.

No library crate may create global mutable state or independently spawn an untracked task.

## 4. Supervised task tree

```text
server supervisor
  +-- HTTP/gRPC listener supervisor
  +-- WebSocket listener supervisor
  +-- authority/route lease supervisor
  +-- outbox worker supervisor
  +-- scheduler supervisor
  +-- runtime host supervisor
  +-- telemetry exporter supervisor
```

Each child has:

- a cancellation token inherited from the parent;
- a bounded restart policy;
- a startup readiness result;
- a shutdown deadline;
- a stable metric and error classification;
- no detached task that can outlive the service instance.

Unexpected termination of a mandatory child removes readiness and initiates fail-closed drain unless an approved restart budget remains.

## 5. Request context

Every public request is converted to a bounded internal context:

```text
RequestContext {
  request_id,
  trace_id,
  project_id,
  user_id?,
  username?,
  session_id?,
  session_family_id?,
  connection_id?,
  node_id,
  received_at,
  deadline,
  compatibility_profile,
  locale/timezone metadata,
  redaction policy,
}
```

Identity fields use strongly typed identifiers, not arbitrary strings. Transport headers or token claims never directly become database predicates without validation and canonical conversion.

## 6. Command and query split

Commands and queries have separate traits and resource policies.

```text
CommandHandler<C> -> Result<Committed<R>, DomainError>
QueryHandler<Q>   -> Result<R, DomainError>
```

A committed command result contains the exact receipt identity needed for ambiguous-response replay. Handlers cannot return externally visible success before durable commit. Query handlers declare consistency and staleness requirements; a cache or search index cannot silently substitute for a linearizable read where the public contract requires one.

## 7. Persistence API

The persistence boundary is asynchronous and deadline-aware. It must express profile-independent invariants while allowing profile-specific retry implementation.

Conceptual contract:

```text
trait TransactionRepository {
  begin_serializable(context) -> Transaction;
}

trait Transaction {
  load_entity_for_update(entity) -> EntityHead;
  find_command_receipt(entity, command) -> Option<Receipt>;
  compare_and_set_head(expected_revision, expected_generation, next) -> bool;
  append_events(contiguous_events);
  append_outbox(ordered_intents);
  insert_receipt(receipt);
  commit() -> CommitIdentity;
}
```

Requirements:

- pool acquisition is inside the total request deadline;
- statement and lock timeouts are explicit;
- cancellation never changes a committed result into an uncommitted assumption;
- SQLSTATE classification is stable but retry policy is owned by a bounded driver;
- the driver re-evaluates deterministic transition inputs safely;
- PostgreSQL deadlocks and CockroachDB transaction restarts are separately tested;
- transaction code performs no provider, network, filesystem or telemetry export I/O.

## 8. Serializable retry budget

Retry is permitted only when all effects before commit are deterministic or repeatable. A request-level budget specifies:

- maximum attempts;
- maximum elapsed time;
- exponential/backoff class and jitter source;
- retryable SQLSTATE/profile errors;
- non-retryable domain and constraint errors;
- metrics for attempts, exhaustion and final outcome.

A retry driver may return `Unavailable` or `Aborted` when its budget is exhausted; it must not fabricate success. After an ambiguous commit result, it first queries the command receipt using the stable command identity before attempting a new write.

## 9. Transactional outbox

Outbox state is explicit:

```text
pending
 -> leased(owner, generation, expires_at)
 -> applied(receipt_digest)
 -> dead_letter(reason_digest)
```

A leased item may return to pending only by an atomic retry/reclaim transition. Reaching the attempt limit becomes a terminal dead-letter transition with a stable reason; it cannot leave an item stranded in leased state. Every apply is fenced by owner and lease generation. A repeated identical receipt is idempotent; a different receipt for an applied intent is data loss.

Workers use bounded batches, writer queues, concurrency, provider deadlines and circuit breakers. External value effects additionally use reconciliation and quarantine rather than blind retry.

## 10. Protocol adapters

Protocol adapters own public compatibility details:

- path/method and protobuf service selection;
- JSON field names, defaults, enum and integer mapping;
- headers, compression and content types;
- gRPC status/details and HTTP error envelopes;
- realtime CID, envelope, opcode and close reason;
- size, rate, heartbeat and idle limits;
- public error text and internal redaction.

Adapters call versioned internal commands/queries and cannot reach repositories directly. Compatibility and native hardened profiles are explicit configuration choices; a hardened difference never masquerades as exact compatibility.

## 11. WebSocket and presence model

A connection actor owns:

- immutable connection identity;
- authenticated session/user/project identity;
- connection generation and revocation epoch;
- bounded inbound and outbound queues;
- heartbeat/idle deadlines;
- subscriptions and presence joins;
- reconnect cursor state;
- a single writer task.

Cross-node routing uses a distributed registry with route generation. Fanout carrying an older route or revocation epoch is rejected. Slow consumers follow a documented bounded policy: drop non-contract data only where allowed, otherwise close with the exact profile behavior.

## 12. Runtime host

Runtime invocation is isolated from server internals through capability traits. Each profile declares:

- supported language/engine version;
- initializer/hook/module denominator coverage;
- memory, fuel, CPU and wall-clock budgets;
- deterministic clock/random/provider injection rules;
- allowed network/storage/social/match capabilities;
- module ordering and registration conflicts;
- panic/trap/error mapping;
- concurrency and reentrancy rules;
- cold/warm lifecycle and cache boundaries.

Runtime jobs and hooks cannot retain mutable transaction handles across await/network boundaries. An authoritative match tick runs in an isolated scheduler so one slow match cannot delay unrelated matches.

## 13. Configuration model

Typed config is assembled in deterministic precedence order matching the declared profile:

```text
compiled defaults
 -> config file(s)
 -> environment mapping
 -> CLI flags
```

Every field records source and redacted effective value. Unknown keys, invalid combinations and deprecated aliases have exact profile behavior. Secrets are references resolved through a provider; diagnostics reveal only provider/key identifiers and epochs, never secret bytes.

Config changes are classified:

- immutable until restart;
- reloadable with validation and rollback;
- per-project/versioned;
- forbidden in compatibility profile.

## 14. Lifecycle and readiness

Startup order:

1. process/resource validation;
2. telemetry/redaction;
3. config and secrets;
4. database pools and schema ABI;
5. ownership/outbox/runtime dependencies;
6. listeners;
7. readiness.

Drain order:

1. remove readiness and stop new admissions;
2. notify/close realtime connections according to profile;
3. stop schedulers and new authority acquisition;
4. finish or cancel bounded in-flight commands;
5. release/quarantine leases;
6. flush bounded telemetry;
7. close pools and exit before the shutdown deadline.

Liveness reports process health only. Readiness reports ability to safely accept the declared traffic class. A schema mismatch, mandatory child failure or missing authority dependency removes readiness.

## 15. Observability contract

Every request, transaction, authority transition and outbox effect records stable low-cardinality dimensions. User IDs, tokens, payloads, receipts and provider secrets are not labels. Logs use structured redaction classes and event IDs. Traces can link command, transaction and outbox identities without exposing secret material.

Required operational signals include:

- admission and response totals by stable operation/status;
- queue depth, drops, closes and saturation;
- DB pool acquisition, transaction attempts, SQLSTATE classes and latency;
- ownership generations, takeovers and stale-write rejections;
- outbox pending/leased/applied/dead-letter/reclaim;
- session refresh/replay/revoke and socket disconnect;
- runtime invocation budget, trap and timeout;
- readiness reason and shutdown phase.

## 16. Test layers

Each component participates in the applicable layers:

1. unit and compile-time contracts;
2. property/model tests;
3. malformed/fuzz corpus;
4. live PostgreSQL and CockroachDB tests;
5. protocol and database differential;
6. deterministic fault injection;
7. process restart and ambiguous commit;
8. node loss/partition/lease takeover;
9. performance and endurance;
10. backup/PITR/restore and migration rehearsal;
11. security review and penetration testing.

A test that returns early because its environment is absent is a developer skip only. A required CI lane sets an explicit required flag and fails on missing prerequisites.

## 17. Dependency policy

Dependencies must be pinned through `Cargo.lock`, licensed, provenance-checked and covered by advisory/SBOM gates. Security-sensitive primitives prefer reviewed ecosystem libraries. Any unsafe code, FFI, VM, crypto or parser dependency requires an ADR, owner, fuzz plan and independent review.

## 18. Vertical-slice exit criteria

The first Rust vertical slice exits only when:

- `trnm-server` compiles under the pinned toolchain with warnings denied;
- config/CLI/migrate/health/readiness/shutdown are tested;
- one HTTP/gRPC and one realtime operation pass exact adapter vectors;
- one command commits entity/event/receipt/outbox atomically in both database profiles;
- response loss, process restart, stale revision/generation and stale outbox worker pass;
- target-native exact-head CI artifacts exist;
- immutable oracle differential has no unexplained P0/P1 divergence;
- evidence is indexed and independently accepted;
- no broader compatibility, production or replacement claim is inferred.
