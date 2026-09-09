# Operations and release

Status: **authoritative current documentation**  
Revision: 2026-09-01

## 1. Operational status

The repository is not production-ready and has no public-online or replacement authority. The current broad runnable topology remains official Nakama with the Go migration input. Rust server and database workflows are source/evidence candidates used to prove bounded slices.

No runbook may describe a candidate as production merely because its process starts or a local database test passes.

## 2. Configuration

Target precedence is deterministic and profile-specific:

```text
compiled defaults
 -> configuration files
 -> environment mapping
 -> CLI flags
```

Every field has type, default, range, source, reload class and redacted effective value. Unknown keys and invalid combinations fail according to the declared profile. Secrets are provider references rather than ordinary values.

The current database-backed server has explicit environment configuration for bind addresses, database profile/URL/TLS, pool/timeouts, schema source commit, administrator token, optional session auth and request limits. Non-loopback and plaintext database modes require explicit candidate opt-in.

## 3. Startup and readiness

Target startup order:

1. validate process/resource limits;
2. initialize redacted telemetry;
3. load configuration and secret handles;
4. create database pools and validate connectivity;
5. validate migration digest and schema ABI;
6. initialize authority, outbox, runtime and provider dependencies;
7. start protocol listeners;
8. publish readiness.

Liveness reports process health only. Readiness means the process can safely accept the declared traffic class. Schema mismatch, failed mandatory child, missing authority dependency, exhausted pool or active drain removes readiness.

The current PostgreSQL test harness waits for the final post-initialization server and executes SQL rather than accepting one transient readiness probe. CockroachDB readiness is independently verified.

## 4. Drain and shutdown

Target drain order:

1. remove readiness and atomically stop new mutation admission;
2. close or notify realtime connections according to profile;
3. stop new schedules and authority acquisition;
4. finish or cancel bounded in-flight operations;
5. release or quarantine leases;
6. flush bounded telemetry;
7. close pools and exit before the shutdown deadline.

Operations admitted before the drain fence may complete under their original deadline; no operation is newly admitted after drain acknowledgement. Existing WebSockets, idle/control-only sockets, HTTP and gRPC share the same process drain state.

Current source includes a shared drain candidate and deterministic tests, but full OS-signal, cancellation and production shutdown evidence remain open.

## 5. Database profiles

PostgreSQL and CockroachDB are separate operational products. Each requires its own:

- immutable test and release image identity;
- migration and schema digest;
- connection/TLS policy;
- transaction retry behavior;
- backup/restore/PITR method;
- capacity and failover profile;
- upgrade/downgrade support matrix;
- incident runbook and evidence.

`migrations/postgresql/` and `migrations/cockroachdb/` are the only production DDL chains. An adapter that does not match the migration ABI fails readiness.

## 6. Pool, timeout and cancellation

The current source bounds maximum/minimum pool size, acquisition, idle/lifetime, statement, lock and idle-transaction timeouts. Serializable retries have attempt, elapsed-time and jitter limits.

Production acceptance additionally requires:

- request deadline propagated through acquisition and every operation;
- cancellation of already-running blocking SQL on deadline or shutdown;
- safe ambiguous-commit reconciliation before any retry;
- pool saturation and connection churn evidence;
- certificate reload/rotation evidence;
- separate PostgreSQL deadlock and CockroachDB restart proof;
- metrics and alert thresholds.

## 7. Outbox operations

Workers use bounded batches, concurrency and provider deadlines. Every lease and transition is owner/generation fenced. Operational views distinguish pending, leased, applied, dead-letter, retry/reclaim and reconciliation states.

Crash-before-publish and crash-after-publish are separate failure boundaries. An ambiguous provider result is quarantined/reconciled; it is not blindly retried when duplicate value is possible. Dead-letter is a terminal state with a stable reason, not a stranded lease.

## 8. Observability

Required low-cardinality signals include:

- request/admission/result totals by stable operation and status;
- queue depth, saturation, drops and socket closes;
- pool state, acquisition failures, transaction attempts, SQLSTATE class and latency;
- authority generation, takeover and stale-write rejection;
- outbox pending/leased/applied/dead-letter/reclaim/reconciliation;
- token refresh/replay/revoke and socket disconnect;
- runtime budget, trap and timeout;
- readiness reason, child health and shutdown phase;
- build, config and schema identities.

User IDs, tokens, payloads, receipts and provider secrets are not labels. Logs use stable event IDs and redaction classes.

## 9. Backup, restore and PITR

Each profile requires:

- backup from the exact schema/migration identity;
- restore into a clean target;
- catalog and semantic comparison;
- command receipt/event/outbox/authority invariants;
- encryption and access control;
- RPO/RTO measurement;
- corruption and missing-object failure behavior;
- retention and deletion policy;
- regular rehearsal.

A logical export/rebuild smoke does not equal PITR. Backup success without a tested restore receives no operational credit.

## 10. HA and failover

Production topology requires multi-node authority, route and outbox fencing. Test node loss, process crash, partition, delayed messages, lease takeover, stale route, reconnect storm, database failover and rolling restart. No two nodes may accept authority for the same generation.

Correctness assertions continue during load/failover. Availability results cannot mask duplicate writer, acknowledged-write loss or duplicate value.

## 11. Capacity and endurance

Every supported profile defines hardware, topology, workload mix, CCU/request/match/storage rates, latency/error/SLO targets and cost. Compare on equivalent hardware with the oracle where relevant.

Required endurance may include 24h, 72h and 7d. Track memory growth, queue/pool stability, reconnect behavior, scheduler drift, outbox backlog, database compaction/storage growth and error accumulation. A shorter run does not substitute.

## 12. Migration and rollback

Migration stages are:

```text
nakama_primary
 -> rust_shadow_no_effect
 -> rust_canary_new_entities
 -> rust_primary_new_entities
 -> nakama_read_only
 -> nakama_retired
```

Data flow uses snapshot/backfill plus durable CDC/outbox receipts, not synchronous dual writes in request handlers. Each phase defines ownership, validation, rollback point, active-entity disposition and abort thresholds.

Rollback must account for sessions, parties, tickets, matches, schedulers, IAP transactions, outbox effects and Rust-only schema state. Crossing an irreversible barrier requires explicit approval and evidence.

## 13. Release gates

A release candidate requires:

- frozen exact source and upstream identities;
- complete required current-head and prospective-merge checks;
- dependency lock, advisory, license, SBOM and signed provenance;
- migration/restore/rollback artifacts;
- security and penetration review appropriate to exposure;
- profile-specific capacity, HA and endurance results;
- no unexplained P0/P1 divergence;
- accepted evidence and release committee decision.

Release artifacts are immutable and signed. Container tags are accompanied by digests. Generated source and schema identities are included.

## 14. Shadow and canary

Shadow is read/observe only: it does not sign tokens, join production pools, broadcast, settle value or mutate authority. Canary assigns exclusive ownership by an explicit cohort key; the same entity/session/effect cannot be writable in both systems.

Canary aborts on identity/permission/value divergence, duplicate writer, data loss, outbox reconciliation failure, SLO breach or rollback uncertainty. Promotion requires a reviewed observation window and successful rollback rehearsal.

## 15. Retirement

Nakama retirement is allowed only after:

- no new traffic routes to Nakama;
- no active authority, route, session key or scheduler remains there;
- all data and Go module source are migrated or explicitly disposed;
- rollback/retention obligations are satisfied;
- C5 and operational support are approved;
- secrets are rotated/revoked;
- final backups, audit and decommission evidence are accepted.

Deleting a process, branch or Go source before these conditions does not constitute retirement.

## 16. Incident boundary

An incident change may contain active harm using the smallest auditable change, retained refs and tests. Normal gates are restored immediately afterward and an independent post-incident review is required. Emergency bypass cannot promote compatibility, production or retirement claims.
