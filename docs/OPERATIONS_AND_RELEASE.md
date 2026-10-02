# Operations and release

Status: **authoritative current documentation**  
Revision: 2026-10-03

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

The schema CLI requires the effective default namespace: `pg_catalog,public` for PostgreSQL and `pg_catalog,pg_extension,public` for CockroachDB. A custom role or URI search path fails migration before DDL; configure the isolated migration connection explicitly rather than allowing schema names to redirect historical statements.

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

## Storage schema v2 upgrade candidate

`trnm-schema migrate --candidate-plaintext` invokes the same locked migration engine used by the canonical server. Set `TRNM_DATABASE_URL`, `TRNM_DATABASE_PROFILE` and a 40-character `TRNM_SCHEMA_SOURCE_COMMIT`; the optional audit input is `TRNM_SCHEMA_APPLIED_AT_MS`. This candidate plaintext CLI is for isolated development and migration tests; production TLS/secret custody and operator acceptance remain separate. Database URLs are not printed. Output binds schema version, full-chain digest algorithm and value, writer epoch, original foundation commit, upgrade commit and genuine v2/v3 apply publishers. The current profile chain contains four files through schema 4/epoch 4 and twelve authoritative tables.

Fresh databases apply the complete ordered chain. An existing v1 database additionally requires `TRNM_STORAGE_LEGACY_WRITER_ROLE`: drain that role's writer process, revoke storage INSERT/UPDATE/DELETE and column write privileges available directly, by inheritance or through reachable roles, then verify old-session writes are rejected before invoking migration. Revoke PostgreSQL TRUNCATE/TRIGGER and CockroachDB CREATE/DROP access as well; CockroachDB CREATE can authorize ALTER TABLE and DROP can authorize TRUNCATE. PostgreSQL SET ROLE access is checked separately from inherited grants. PostgreSQL role ADMIN OPTION is conservatively rejected because it can restore an otherwise revoked SET or inherited path; remove that administration capability as part of the barrier. The CockroachDB barrier conservatively checks reachable memberships. The engine refuses an owner, superuser/admin or an unfenced role and does not revoke privileges or terminate production sessions. A role check cannot replace an operator-controlled inventory and drain of all other writers. Read-only startup verification preserves the original metadata provenance and rejects partial, future or incompatible schema identities.

PostgreSQL publishes the append DDL and identity atomically. CockroachDB schema changes are not claimed as one atomic multi-statement transaction: each declared action must become visible, only a valid completed prefix may resume, and identity is published last. Preserve the lock, metadata and catalog report for interruption recovery. Original rows keep unknown timestamps as NULL; backups, semantic snapshots and restore tests must preserve both known microsecond values and NULL rather than converting them to epoch or upgrade time. No DROP-based rollback or return to a v1 writer is authorized. A forward fix needs the same exclusive writer barrier, profile-specific recovery validation and independent review.

Schema3 conversion requires the same independently inventoried and drained writer barrier as schema2. Run the typed migration engine; direct execution of `0003_storage_jsonb_up.sql` omits its bounded backfill directive and cannot publish serve readiness. Invalid old binary/UTF8/JSON data requires custody-preserving repair before retry, not normalization or dropping rows. Keep all original bytes, all old metadata fields, the full catalog and the complete four-file lock in current recovery evidence; retain the historical three-file identity when identifying a v3 prefix. PostgreSQL conversion is transactional; CockroachDB completed prefixes and valid already-converted rows may resume, while mismatched partial rows fail. Keep the original foundation source/time and genuine `v2_apply_source_commit`; never replace them with a later verification binary's SHA. Metadata stays at the prior revision until complete native postconditions pass. After epoch3 publishes, forward repair under the writer barrier is the only source-supported recovery path. Native-source tests, a manifest hash and successful CI do not constitute production migration or full Nakama replacement acceptance.


## Storage source transfer operations candidate

Schema 4 adds `0004_storage_source_import_up.sql`, widens only the stored key/ACL domain and adds manifest-bound job/page journals. An existing v3 upgrade preserves its recorded publisher in `v3_apply_source_commit`, alongside genuine v2 history and the original foundation source/time. Fresh migration records the actual intermediate publications. Preserve the complete four-file profile lock and all twelve-table catalog/data snapshots; do not rewrite a historical publisher to the current verify binary. PostgreSQL 17.6 pg_dump/pg_restore changes the deparsed grouping of the jobs-count and pages-bound CHECK constraints. Read-only verification accepts exactly the two recorded complete descriptors for each named constraint and preserves their raw observations. Changed bounds, predicates, keys, validation state, profile or other catalog objects remain rejected; other PostgreSQL versions require separate qualification. The v2/v3 decisions above and the original six schema-upgrade regressions plus 41 v3 family observations remain mandatory coverage. No older writer or destructive downgrade is authorized after epoch 4.

Use the existing `trnm-storage-export snapshot PACKET_DIRECTORY --candidate-plaintext` only with an isolated source-DDL fixture or an independently authorized custodian snapshot. Supply database URL/profile, page size, source execution class, explicit producer commit/tree/execution ID, actual exporter source-file SHA256, executing-binary SHA256 and the directory holding pinned initial SQL/core-storage/LICENSE. The exporter creates a new private directory; it never overwrites a prior packet. PostgreSQL exports one read-only repeatable-read snapshot. CockroachDB exports one read-only serializable transaction identified by its native clock, without claiming an AS OF/MVCC snapshot token. Exact native timestamp equality rejects precision loss. All private/global rows and lawful JSONB shapes are exported without an ACL filter or original-request MD5 reconstruction. Manifest/receipt completion follows successful native read-only commit and file/directory synchronization; failed packets remain incomplete.

Before target acquisition, run `trnm-storage-import verify-packet PACKET_DIRECTORY` with independent `TRNM_STORAGE_EXPECTED_MANIFEST_SHA256`, `TRNM_STORAGE_EXPECTED_RECEIPT_SHA256`, producer source/binary hashes, commit/tree and execution ID. Those values must come from the authorized custody path, not be copied from the packet. Local file hashes, matching self-description or a nonzero source hash cannot establish that a source was Nakama. The source-DDL fixture is not an oracle. Production signature/issuer trust and secret/transport custody are not implemented by this plaintext candidate. The exact variable names and grouping are recorded in `docs/DEVELOPMENT.md` and the binaries' `--help`.

Every database import mode additionally requires `--candidate-plaintext`, `TRNM_DATABASE_URL`, `TRNM_DATABASE_PROFILE`, `TRNM_STORAGE_IMPORT_AUDIT_AT_MS`, `TRNM_STORAGE_LEGACY_WRITER_ROLE` and independently supplied `TRNM_STORAGE_EXPECTED_TARGET_SCOPE_SHA256`. Use a dedicated empty target or the exact registered import prefix, with no outbox rows in any state. Existing completed or dead-letter rows are also rejected; do not delete history to bypass this candidate restriction. A lease becoming expired or terminal is not proof that an old worker has stopped external delivery. Production worker inventory, stop and join remain operator obligations. Inventory and drain every old writer; revoke all direct/inherited/reachable storage write and destructive privileges before preflight. The declared legacy role must have no other active database session. It must also have no direct, inherited or reachable role mutation/destructive privileges or ownership on storage, schema metadata or either import journal, and no reachable public-schema or current-database ownership. PostgreSQL TRUNCATE/TRIGGER and CockroachDB actual CREATE/DROP/TRIGGER/ALL descriptor grants are checked against their native catalogs; unknown catalog authority is rejected. Existing noninternal triggers on these four tables also block import, including deferred triggers that could run after row reconciliation. Inventory and remove them through a separately reviewed forward change; the importer never drops or disables them automatically. The tool checks this role but does not revoke privileges, terminate writers or prove an inventory of all principals. PostgreSQL target identity includes its native system identifier and database identity; CockroachDB explicitly reports `namespace-and-external-scope-only`, with physical cluster identity unobserved.

`preflight` verifies the entire packet against current native schema, target identity/collation, owner/key/ACL/time/value domains and the empty or reconciled prefix before any job/data mutation. Source and target profiles must match. PostgreSQL requires UTF8/libc locale equality and actual deterministic default collation on both keys; CockroachDB requires uncollated source/target keys. Unsupported or mismatched bindings fail without registration. Imported native values retain opaque versions and original exported times as `nakama-export-unknown-request`, with NULL request witnesses and the source manifest digest; no request-byte MD5, NULL-history timestamp or normalized key is fabricated.

`begin` registers exactly that checked source/target/audit identity. `apply-page` commits one serializable transaction containing its rows, page receipt and full old-checkpoint CAS. `apply` completes proven remaining pages and calls finalization within its operation budget. `resume` verifies the exact durable prefix read-only after a response loss or restart; it neither skips a conflicting page nor repairs data. `finish` sets completion only after all pages and the complete native target inventory reconcile. `verify-applied` additionally requires persisted completion. Keep packet bodies, job/page receipts, prefix digest and target metadata together for recovery; a different manifest, target guard, audit input or conflicting native row fails closed. There is no implicit transaction retry or automatic compensation.

An incomplete job blocks ordinary storage transactions, canonical startup/readiness and pooled business admission, including reads. PostgreSQL mutable import stages additionally rewrite the metadata tuple with identical values after authority and trigger checks, so an older SERIALIZABLE business waiter aborts with 40001. A lock alone does not refresh its snapshot. Schema identity and all recorded publishers remain unchanged; CockroachDB retains its separately tested native FOR UPDATE behavior. Do not bypass this fence to expose a committed prefix. Operators resume the same verified packet under the same writer barrier or pursue a separately reviewed forward repair. No row deletion, normalization, journal reset, DROP-based rollback or production reopening is authorized by the CLI.

Limits are 256 MiB per packet, 10,000 rows, 100 pages/100 rows per page, 16 MiB per native value, 32 MiB summed native-value bytes per page, 16 MiB row metadata and 2 MiB manifest. Pool/operation budgets are 300 seconds and statement budgets at most five seconds/remaining time. Native SQL JSONB rendering still materializes before wire-length guards; database backend memory containment and hard bounds for stalled synchronous calls remain open. Retain exact profile-native execution and independently reviewed custody, recovery and operational evidence before promotion. The connected source transfer does not authorize production, reduce the full Nakama denominator or grant compatibility, cutover, retirement or replacement credit.
