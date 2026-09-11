# Operations and release

Status: **authoritative current documentation**  
Revision: 2026-09-11

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

Workers use bounded batches, concurrency and provider deadlines. Every command transition, lease, publish attempt and acknowledgement is owner-, lease-generation- and authority-generation-fenced. Operational views distinguish pending, leased, applied, dead-letter, retry/reclaim and reconciliation states.

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

## Database capacity and segmented endurance candidate

`scripts/ci-database-capacity-smoke.sh` runs one identical transactional storage upsert/read workload through `pgbench` against either PostgreSQL or CockroachDB and retains transaction count, failed transaction count, average latency, throughput, exact workload digest and candidate identity. `scripts/ci-database-endurance-segment.sh` converts the same execution into a hash-chained segment; `scripts/finalize-database-endurance-ledger.py` accepts only contiguous, same-candidate, same-workload, zero-failure ledgers totaling 24h, 72h or 7d.

A short qualification run proves the workload and evidence machinery, not capacity or endurance. Numerical throughput/latency thresholds and production sizing require independent approval; incomplete or short ledgers receive no duration credit.

## PostgreSQL connection fault candidate

`scripts/ci-postgresql-connection-faults.sh` opens eighty fresh runtime connections, fills an exact role connection limit and proves the next connection is rejected, cancels and terminates separate in-flight transactions and verifies both updates roll back, exercises `statement_timeout`, then requires a new connection to succeed. Evidence binds the image, migration chain and error/output digests.

Role-level exhaustion is not automatically application-pool acceptance. Multi-node failover, capacity targets, performance acceptance, independent review and production readiness remain separate facts.

## PostgreSQL point-in-time recovery candidate

`scripts/ci-postgresql-pitr.sh` enables WAL archiving, takes a physical base backup, records an exact target LSN after an included write, archives later WAL containing an excluded write, then restores into a new data directory with `recovery.signal` and automatic promotion. The restored state must contain the target write, omit the later write and match the canonical target snapshot byte-for-byte.

The measured recovery interval and WAL archive digest are retained. This does not approve RPO/RTO, prove continuous production archive monitoring or regional restore, provide independent acceptance or authorize production promotion.

## PostgreSQL synchronous primary failover candidate

`scripts/ci-postgresql-primary-failover.sh` creates a physical standby with `pg_basebackup`, requires streaming state, changes acknowledgement to `synchronous_commit=remote_apply`, commits the authoritative command/event/outbox transaction, and proves the standby replay LSN covers that acknowledgement. It then stops the primary, promotes the standby, compares canonical state byte-for-byte and writes successfully after promotion.

The measured failover interval is retained, not declared an approved RTO. The packet does not prove automatic failback, production fencing or split-brain controls, repeated regional failure, independent acceptance or production readiness.

## PostgreSQL recovery write fence and outbox quarantine

`scripts/postgresql-recovery-quarantine.sql` runs under an exclusive outbox table lock. It refuses to proceed while any lease is active, streams every ready intent to the retained quarantine artifact, atomically converts those rows to a terminal recovery-quarantine state, and changes new `trnm_runtime` sessions to read-only. The live harness proves that runtime reads remain possible while a new mutation fails.

This is a database-layer source/evidence candidate. Production rollback still requires an upstream routing fence, draining or terminating every existing runtime connection, a conflict-free operator decision, accepted backup/restore evidence and explicit authorization before any write capability is restored.

## PostgreSQL semantic recovery candidate

`scripts/ci-postgresql-semantic-recovery.sh` applies only the authoritative `migrations/postgresql` chain, proves that a repeat application fails without catalog drift, executes ten malformed-row/constraint probes, creates a custom-format logical backup, restores into an empty database, and compares canonical data plus columns, constraints and indexes. The retained manifest binds the database image ID, migration-lock digest, backup digest and source/restored semantic digests.

This packet is a repository-controlled recovery source candidate. It does not establish PITR, approved RPO/RTO, primary failover, multi-node durability, independent acceptance, production readiness, cutover or rollback authorization.

## CockroachDB leaseholder and node failover candidate

`scripts/ci-cockroachdb-node-failover.sh` starts three CockroachDB nodes and waits for three voting replicas. It relocates the authoritative entity range lease to node 1, isolates that leaseholder from the cluster, proves acknowledged state is unchanged and writes through the surviving quorum. It then reconnects node 1, stops a non-leaseholder node, writes again, restarts the node and requires it to observe both commits.

Measured recovery intervals are evidence values, not approved RTOs. The lane does not prove regional or long-duration partitions, production topology, independent acceptance or production readiness.

## CockroachDB semantic recovery candidate

`scripts/ci-cockroachdb-semantic-recovery.sh` is a distinct CockroachDB profile. It applies only `migrations/cockroachdb`, proves repeat application rejection without catalog drift, executes ten malformed-row/constraint probes, performs native `BACKUP DATABASE` and `RESTORE DATABASE` through `nodelocal`, and compares canonical data plus `SHOW CREATE ALL TABLES` output. Its manifest binds the database image ID, migration lock, backup-directory digest and source/restored semantic digests.

PostgreSQL evidence is not inherited. This packet does not establish node or leaseholder failover, approved RPO/RTO, independent acceptance, production readiness, cutover or retirement.

## Cutover and retirement proposal validator

`scripts/cutover-state-machine.py` validates only that an untrusted request describes the ordered sequence planning → shadow → exclusive canary → production → retirement pending → retired and carries the expected candidate, gate, blocker, rollback and claimed-review fields. It does not advance the authoritative state. The output preserves the current state and history, adds a `pending_proposal`, and keeps `public_online`, `cutover_authorized` and `nakama_retired` false.

The repository currently has no trusted cutover-authority receipt verifier or durable nonce/replay store. `materialize_transition` therefore fails closed. Reviewer logins, conflict attestations, local signatures, blocker booleans, claimed protected admission, matching digests, administrator power and replayed proposal files cannot authorize shadow, canary, production or retirement.

`scripts/derive-cutover-blocker-packet.py` derives a diagnostic blocker packet from the gap register and marks it `may_authorize_transition=false`. Real transition authority requires an externally authenticated stable principal, qualified role and conflict decision, exact candidate/evidence binding, issued-at/expiry, unique nonce, durable anti-replay verification, platform-native protected-admission readback and the applicable operational decision. Source code or CI success cannot manufacture those facts.
