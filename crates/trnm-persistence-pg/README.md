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
cargo test -p trnm-persistence-pg --locked --bin trnm-server live_cockroach_serialization_failure_retries_entire_command -- --nocapture
```

The second command requires the isolated live database environment and explicit
required flag in its workflow to earn execution credit. Both workflows retain
source/unit and live jobs, nonempty-result assertions and exact definition pins.
