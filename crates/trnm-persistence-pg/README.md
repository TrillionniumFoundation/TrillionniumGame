# trnm-persistence-pg

Status: **module documentation; http-grpc-websocket-database-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-persistence-pg`  
Workspace class: `root`
Lifecycle: `product-adapter-and-temporary-composition`  
Owner role: `database-migration`

## Status and authority

This document is the current module-level engineering contract for `trnm-persistence-pg`. Its authority is limited to the module boundary described here: **authoritative PostgreSQL/CockroachDB adapter and temporary database-backed server binary**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `http-grpc-websocket-database-source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Serializable persistence, CAS, receipts, outbox rows, database profiles, pool policy, migrations, and the current database-backed vertical slice.

Non-goals: Its embedded server location is temporary and must not become a permanent coupling between process supervision and persistence.

## Architecture and dependencies

It implements core repository contracts and composes selected token, session, realtime-wire, HTTP, gRPC, WebSocket, and migration components.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

The only production DDL authority is migrations/. PostgreSQL and CockroachDB are separate profiles with separate evidence and retry behavior.

The shared `trnm-schema` runner consumes the locked, ordered 0001/0002/0003/0004 chain. The current ABI is schema 4/writer epoch 4 with twelve authoritative tables; original historical SQL bytes and prefix identities stay immutable.
Schema version 2 appends nullable storage `create_time`/`update_time` columns
without defaults or historical backfill, plus chain identity and storage writer
epoch metadata. Serve verification reads existing identity and catalog; it does
not migrate or rebind them. Populated v1 upgrades require the declared legacy
writer role to lose effective storage write privileges before publication.
The epoch check supplements that barrier and does not fence an unchecked old
writer by itself. Recovery uses a verified forward fix rather than a column drop
or metadata downgrade.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Commit/replay acknowledgement, revision and generation fencing, bounded serializable retry, response-loss recovery, and outbox terminal behavior are mandatory.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

Production database transport requires verify-full TLS and reviewed credential providers. Plaintext is limited to explicit loopback development evidence.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-persistence-pg --all-targets --locked
cargo clippy --package trnm-persistence-pg --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## TLS test endpoint readiness

The `pg-tls-rotation` live lane must not accept the initialization server's Unix socket as the final endpoint. `scripts/wait-postgresql-tls-ready.py` connects explicitly to container TCP `127.0.0.1` with `sslmode=verify-full` and the profile's read-only mounted root certificate. It executes SQL against the requested test database and requires `ssl=on`, a non-recovery server, and TLS on its own `pg_stat_ssl` session. A transient SQL failure retries inside one monotonic deadline; a stopped or unavailable container fails. Empty, extra, non-TLS or failed-query output never earns readiness, even if the process prints `ready`.

The default total budget is 60 seconds per endpoint, with a validated maximum of 300 seconds. Each subprocess receives at most three seconds and never more than the remaining budget. libpq connection and SQL statement budgets are separately two seconds. A success arriving at or after the total deadline is rejected. Subprocess diagnostics, password values and connection URLs are not emitted by the helper. The ephemeral test password is forwarded by the environment variable name, not embedded in subprocess argument values. Published container ports bind only to host loopback.

```bash
python3 -m unittest tests.control_plane.test_pg_tls_endpoint_readiness -v
```

The deterministic suite exercises initialization transition, failed SQL, actual command construction, stopped containers, per-operation and total timeouts, late success, invalid input and diagnostic redaction. It is a mocked prerequisite regression, not a live database or TLS-rotation result. The separate Rust probe must still execute old/new-root success, cross-root rejection and invalid-root rejection. The workflow manifest requires both source/unit and live execution jobs; a successful unit job cannot substitute for a skipped live job. This repair changes no authoritative DDL, public API, receipt, rollback authority or production claim.

## Deadline lane trigger coverage

The mandatory `pg-operation-deadline` pull-request trigger has no path or branch filters. An unfiltered PR trigger covers pool parts, service adapters and the regression suites without listing them twice. The separate `push` trigger remains restricted to `main` and retains explicit source paths. Counting occurrences of a path across the whole YAML document does not establish either event's coverage.

`validate_required_pr_and_main_paths` in `scripts/workflow_trigger_contract.py` validates these distinct contracts. Both the deadline source checker and the cancellation lifecycle suite use it. It accepts the bounded canonical trigger mapping, requires the normal opened/synchronize/reopened PR activities, rejects PR selectors, and checks required positive patterns inside the actual main-push mapping. Duplicate events/selectors/patterns, negative exclusions, aliases, unsupported complex forms and missing main paths fail closed. Text in another event, a comment or a job cannot substitute for a trigger. General YAML syntax and exact workflow-blob checks remain independent requirements.

```bash
python3 scripts/check-pg-operation-deadline.py --self-test
python3 -m unittest tests.control_plane.test_pg_deadline_trigger_coverage -v
python3 -m unittest tests.control_plane.test_pg_cancellation_lifecycle -v
```

These are source and regression checks, not live cancellation evidence. The existing Rust deadline/shutdown tests must still run against PostgreSQL, prove backend retirement and subsequent pool usability, and retain non-empty exact-candidate results. This trigger-contract repair changes no runtime code, workflow job, permission, timeout, DDL, receipt identity or acceptance gate.

## Operations

Pools, acquisition, statements, locks, transactions, retries, readiness, drain, outbox, and profile identity require bounded metrics and failure reasons.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Pool/TLS cancellation, persistent realtime, complete gRPC/gateway, session integration, outbox delivery, HA/PITR, load, SDK, and oracle evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-SERVER-001`
- `GAP-P0-DATA-001`
- `GAP-P1-PG-001`
- `GAP-P1-TEST-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.


## Database negative attribution and production retry proof

The TLS rotation binary now distinguishes a bounded, credential-free OpenSSL
X509 verification witness from native-tls pool admission. Only issuer/chain
verification codes qualify for a cross-root failure. Expiry, hostname, protocol,
connection, deadline, authentication and SQL failures cannot substitute. Each
negative is bracketed by fresh witness and authenticated pool/SQL controls on the
same single numeric loopback endpoint. The pool must also refuse the rejected
root. A malformed PEM is a local parser rejection, not remote TLS evidence.
The independent witness does not expose or change the production TLS connector.
Its TCP/SSLRequest/TLS I/O shares one two-second deadline; PEM reads are capped.
The existing pool's stalled-operation limitations still apply separately.
OpenSSL 0.10.81 was already locked transitively through native-tls; its direct
use is confined to this diagnostic binary. No dependency version is upgraded.

The Cockroach retry test retains the natural write-skew classifier/supervisor
phase, and additionally executes the real RetryingRepository -> PooledRepository
-> PgRepository transaction path against the authoritative migration. A dedicated
one-connection test pool enables Cockroach's session commit-error injection for
the first attempt, then disables it before retrying. Before retry it asserts zero
receipt/event/outbox/link rows and an unchanged entity head. Successful retry must
produce exactly one of each and preserve the complete command identity. A fresh
pool must replay the real durable receipt, while a changed fingerprint fails.
Repeated commit faults must exhaust the retry budget without partial effects.
The injection is test-only, confined to a newly created disposable loopback
database, and does not introduce a production fault hook or manufactured receipt.
This is commit-boundary fault evidence, not natural contention within the entire
production transaction, actual network response-loss injection, multi-node HA,
PITR, endurance, independent acceptance or complete Nakama compatibility.

Focused checks:

```bash
cargo test -p trnm-persistence-pg --locked --bin trnm-pg-tls-rotation-probe
cargo test -p trnm-persistence-pg --features diagnostic-compat-server --locked --bin trnm-pg-compat-server live_cockroach_serialization_failure_retries_entire_command -- --nocapture
```

The second command requires the isolated live database environment and explicit
required flag in its workflow to earn execution credit. Both workflows retain
source/unit and live jobs, nonempty-result assertions and exact definition pins.

## Authority lease and storage adapters

The adapter owns typed SQL access to all twelve authoritative tables, including manifest-bound storage import jobs and pages. Authority lease acquisition locks the entity head and lease in a serializable transaction; an expired-owner takeover advances both lease and authority generations before a new owner is returned. Renewal and release require the exact entity, owner, lease generation and authority generation.

Storage reads and mutation batches use `trnm-storage-core` public version, integrity, OCC and ACL types. Batch keys are locked in deterministic order and every write/delete commits in one serializable transaction. Generated write ACK versions use MD5 of exact request bytes. Stored `PublicVersion` is independently retained and may be opaque or empty; native JSONB rendering never reconstructs request MD5.

`read_storage_objects_with_metadata`, `apply_storage_batch_with_metadata` and
`list_storage_objects_nakama_with_metadata` return stored-object/receipt/page
wrappers with `StorageTimes`; the original core-returning methods project those
wrappers. `StorageTimestamp { seconds, nanos }` preserves database microseconds
with checked pgwire decoding, including pre-epoch normalization. Infinity,
protobuf years outside 0001–9999 and invalid nanoseconds fail as `DataLoss`.
Historical SQL NULL stays `None` and is never replaced by an epoch or the legacy
`updated_at_ms` value.

Storage inserts explicitly take the transaction database clock for both times.
Updates retain creation, including NULL, and set update from that transaction.
A blind same-public-token/ACL write returns the locked row without UPDATE after
ACL, OCC and integrity checks; Exact writes still update. Mutation transactions
read schema/epoch/chain identity before writing without a global metadata row
lock, and return receipts only after commit. The caller's `updated_at_ms` remains
separate. This narrow storage clock policy does not alter other public times or
assume that transaction clocks increase. Storage operations have no implicit
retry.

`storage_timestamps_database_clock_no_op_and_atomicity` requires an isolated
fully migrated database in mandatory mode. It compares receipts with SQL rows,
covers legacy NULL, real clock pairs, blind/Exact/ACL/content changes, OCC/ACL
rollback, metadata faults and timestamp range rejection on each profile. It
restores metadata and cleans its dedicated rows after success or panic before
printing `storage_timestamps_live_executed profile=...`. The live harness rejects
optional skips; diagnostic execution does not grant independent acceptance.

These paths are source candidates. PostgreSQL/CockroachDB live execution, exact-head evidence admission, profile-specific failover/restore and independent database/storage review remain required before production credit.

## Canonical storage integrity boundary

Storage write callers provide exact value bytes, public OCC intent and ACLs; they do not provide an internal integrity digest. The core model derives request SHA-256, while this adapter separately binds SHA-256 of database-native projection text. Known request witnesses bind request MD5/SHA/length to the native projection; unknown Nakama exports do not invent those witnesses. Returned objects verify the sole native JSONB payload and its projection digest before they are exposed; corruption fails closed as `DataLoss`. Batch-read and client-list SQL filter inaccessible rows before integrity decoding. This is corruption detection, not source authentication or a MAC, and does not replace database access control, encryption, backup validation or immutable-oracle differential evidence.

## Scope-bound storage listing cursor

`PgRepository::list_storage_objects` returns a scope-bound `(StorageActor, Option<UserId>, StorageObjectKey)` tuple rather than a bare object key. The cursor binds the exact `StorageActor`, optional owner filter and last `(collection, object_key, user_id)` key that produced the page. Continuation under a different authenticated actor, owner scope or collection fails closed with `storage_cursor_scope_mismatch`; zero actors and zero owner identities are rejected before SQL execution. ACL filtering remains inside the query before the bounded `limit + 1` sentinel is applied.

This is an in-process source contract. Adapters exposing this typed profile must encode and authenticate the complete cursor tuple and reject tampering, profile changes and cross-project replay. The separate Nakama client-list projection below uses the original unsigned-offset profile and independently applies current principal ACL. The typed Rust value does not by itself establish Nakama wire compatibility, snapshot isolation across concurrent mutations, a stable public cursor format or accepted PostgreSQL/CockroachDB evidence.

## Nakama client-list projection

`PgRepository::list_storage_objects_nakama` accepts only a nonzero user actor and
returns `StorageClientListPage { objects, next }`, where the next
`StorageListPosition { key, user_id, read }` is an untrusted offset. Omitted owner
selects read >= 2 across owners; own owner selects read >= 1;
foreign and explicit zero owners select read == 2 for that owner. Batch read
separately selects read == 2 or owning-user/read == 1. Client write requires
write == 1, client delete accepts write > 0 and the server actor bypasses ACL.
One readonly serializable query applies ACL before the `limit + 1` sentinel.
The three modes order by SQL text read/key/user ID, read/key and key respectively.
Only returned rows are decoded, and the next offset is the last returned row.
Hidden and sentinel rows cannot cause a value/digest/timestamp decoding failure.

The client query admits empty/dot/control collection text up to 4096 UTF-8 bytes;
cursor key offsets have the same byte budget, and read offsets accept every int32.
Returned keys use `StorageObjectKey::new_nakama` for the authoritative 0–128
Unicode-character stored domain. Nonnegative SMALLINT permissions through
32767 are retained exactly, while strict new-request validation stays separate. The old typed API's byte ordering and identifier
validation remain separate. This read-only projection follows the current
locked schema domain and has no automatic retry or source write authority. Pool deadlines, pagination under concurrent writes, exact
profile collation, gateway/gob differences, production source custody and immutable-oracle qualification remain open;
the connected source import below preserves actual exported timestamps.

`nakama_client_listing_modes_cursors_and_integrity_are_database_projected`
requires the selected live profile when `TRNM_REQUIRE_LIVE_DATABASE=1`, checks
three modes and cursor fields, independently seeds Unicode/dot/control rows,
and damages hidden, sentinel and returned digests. Its dedicated namespace is
cleaned after success or panic; a profile-specific execution marker follows the
assertions and cleanup. This source regression does not supply accepted live or
independent review evidence.

Native storage ABI3 separates `value_jsonb`, opaque `public_version`, projection SHA256 and checked known/unknown request provenance. Raw witnesses never supply read/list payloads. SQL returns native text and complete RETURNING rows; even an unknown-history blind no-op validates incoming native JSONB without replacing stored value/times/witness. Actor/ACL/OCC and exact native input/error priority remain profile candidates pending immutable differential. The typed migration preflights all legacy data and checked finite timestamps before revision DDL, preserves foundation/time/prior-v2 publisher, then publishes epoch3 after complete conversion. PostgreSQL owns one atomic transaction; CockroachDB resumes exact DDL and one-row serializable backfill checkpoints.

The 16 MiB projection bound and 32 MiB result/staging bounds constrain returned bytes and Rust allocations. Native database JSONB casts and text materialization happen before their length is observed; these checks do not impose a hard bound on database backend memory. PostgreSQL numeric expansion has separate valid-above-1MiB and gentle-over-16MiB fixtures, with ResourceExhausted and whole-batch rollback assertions. Database-side memory containment, maximum-capacity qualification, production source custody and independent migration acceptance remain open.


## Source-bound native storage transfer

Schema 4 preserves the genuine v3 publisher as `v3_apply_source_commit`, alongside prior v2/foundation provenance. Its append-only fourth SQL adds `trnm_storage_import_jobs` and `trnm_storage_import_pages` and widens stored keys/ACLs without loosening strict internal new-key policy or HTTP validation. Keep the existing six upgrade regressions and all 41 v3 case-family observations; a transfer source fixture cannot replace them.

`export_storage_snapshot(&StorageExportOptions)` returns `StorageExportSummary` after one successful native read-only snapshot and private packet synchronization. PostgreSQL uses repeatable read and a native snapshot identity; CockroachDB uses serializable read-only and a native transaction-clock identity, not AS OF/MVCC custody. The producer checks actual source columns/defaults/constraints/table kind and owner references, includes all global/private rows and lawful JSONB shapes, preserves raw keys/ACLs/versions and verifies exact timestamp equality. Packet members retain exact native value bytes, catalog/query and pinned initial SQL/core-storage/LICENSE, plus actual exporter source bytes. Independent implementation of the fixed export query does not compile upstream Go. Actual source/current-executable hashes are checked, but supplied commit/tree labels do not prove a clean reproducible build.

`verify_storage_export` combines an independent manifest hash, receipt hash and producer source/binary/commit/tree/execution anchors with closed, descriptor-relative packet verification. Only its bounded immutable owned rows reach `preflight_storage_import(packet, StorageImportOptions)`. Preflight requires a same-profile dedicated empty or exact-resume target, independent target scope, native value/time/key/collation validation and drained legacy-writer authority. PostgreSQL binds native system/database identity; CockroachDB reports namespace/external scope only, with physical cluster identity unobserved. Source/target PostgreSQL UTF8/libc locales and deterministic default key collations must match; CockroachDB key collation must be absent. Unsupported bindings fail before registration. The importer fences the declared old role on storage, schema metadata and both journals, including effective table/column writes, PostgreSQL TRUNCATE/TRIGGER, CockroachDB actual CREATE/DROP/TRIGGER/ALL descriptor grants, reachable ownership and public-schema/current-database ownership; unknown catalog authority is rejected. Existing noninternal triggers on those four tables also block import, including deferred triggers; the importer never removes or disables them. All import stages reject any existing outbox row; ordinary command and worker claim/complete/retry transactions share the metadata admission lock. PostgreSQL mutable import stages additionally rewrite the metadata tuple with identical values after authority and trigger checks, so an older SERIALIZABLE business waiter aborts with 40001. A lock alone does not refresh its snapshot. Schema identity and all recorded publishers remain unchanged; CockroachDB retains its separately tested native FOR UPDATE behavior. This dedicated-target restriction does not prove external worker drain, and privileged SQL bypass is outside the service API guarantee.

`begin_storage_import`, `apply_next_storage_import_page`, `resume_storage_import`, `verify_applied_storage_import` and `finalize_storage_import` register, atomically commit pages, reconcile a prefix read-only and verify the full native inventory before completion. Rows, page receipts and full old-job CAS advance together in one serializable transaction; no implicit retry or filesystem I/O enters those mutable transactions. Imported `nakama-export-unknown-request` rows preserve opaque public versions and source times, carry the manifest digest and have NULL request witnesses. No original request or NULL historical time is invented. Incomplete jobs block startup/readiness, pool business admission and every ordinary storage transaction, including reads.

The binaries `trnm-storage-export` and `trnm-storage-import` share `src/bin/storage_transfer.rs`; `docs/DEVELOPMENT.md` lists their exact environment groups and `docs/OPERATIONS_AND_RELEASE.md` records custody, drain and recovery duties. Database modes require `--candidate-plaintext`; packet verification is offline. Export creates a new 0700 directory and 0600 create-new files, with held directory descriptors; it never overwrites an earlier packet. Manifest/receipt completion comes last after native read-only commit and fsync.

Bounds are 256 MiB packet, 10,000 rows, 100 pages/100 rows per page, 16 MiB native value, 32 MiB summed native-value bytes per page, 16 MiB row metadata and 2 MiB manifest. The connected transfer uses 300-second operation/pool budgets and statements at most five seconds/remaining time. Database internal JSONB text materialization and stalled synchronous kernel calls are not proven hard memory/time bounded. Populated NULL-history repair and account/other-domain migration remain separate obligations. Native source-DDL fixtures are not Nakama runtime oracles, and production issuer/signature/transport custody remains unimplemented. These interfaces grant no compatibility, production, cutover or full-replacement acceptance.
