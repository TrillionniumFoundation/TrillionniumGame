# Development guide

Status: **authoritative current documentation**  
Revision: 2026-09-12

## 1. Development contract

All changes are made against the full Rust reimplementation plan in [`../CURRENT_PLAN.md`](../CURRENT_PLAN.md). A source change is not complete until its tests, machine status, evidence boundary and the applicable current topic document agree.

Do not add a new Markdown file to describe a new iteration of an existing topic. Update the current topic document. Git history, pull requests and issues provide history.

The human scope baseline is the development plan v3.1; Plan v3.2 names the subsequent execution tranches, while `plan_version: 3` is the existing machine schema namespace. These labels do not prove a newer compatibility baseline or supersede the full 1.0 denominator. An approved scope revision must update the actual plan and its validators together.

## 2. Repository map

```text
crates/                  Rust cores, adapters and server candidates
runtime/                 current Go plugin migration input and oracle fixture
migrations/              only production-authoritative DDL chain
contracts/               versioned internal/public source contracts
manifests/upstream/       denominator candidates, review requests and locks
oracle/                   immutable/instrumented oracle inputs and tooling
scripts/                  validation, CI, evidence and operational tooling
tests/                    Python contracts and cross-component fixtures
config/                   immutable test-image and runtime policy inputs
database/schema/v2/       non-authoritative design history
docs/                     current human docs plus machine state/evidence
```

The only default server binary is `crates/trnm-server`::`trnm-server`. The database-backed source at `crates/trnm-persistence-pg/src/bin/trnm-server.rs` builds only as the explicit `diagnostic-compat-server` target `trnm-pg-compat-server`. New server behavior must move toward the single composition-root architecture described in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 3. Toolchains

The repository pins Rust through `rust-toolchain.toml` and Cargo lockfiles. CI currently uses Rust `1.85.1`. Go toolchain behavior is governed by `runtime/go.mod`; Python control scripts target the runner Python available on Ubuntu 24.04 and use the standard library unless a reviewed dependency is explicitly introduced.

Containerized database evidence uses immutable image digests from `config/database-test-images.json`. Tags alone are not evidence identities.

## 4. Required local preflight

Run the control plane first:

```bash
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 scripts/engineering_readiness.py
python3 -m compileall -q scripts tools tests
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

`engineering_readiness.py` is a read-only inventory and documentation regression. Exit zero means that its narrow source/document shape agrees. It does not validate gap closure, grant design approval or replace retained-evidence admission. Its output deliberately separates recorded gap statuses, document presence, proposed depth and unassessed independent acceptance. It reads no live credentials and performs no network calls or status writes.

Run the root Rust workspace:

```bash
cargo fmt --all -- --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

All long-lived Rust products and adapters are members of the root Cargo workspace. Only the two temporary JWT differential gate packages remain isolated until an accepted retirement packet exists. The aggregate merge gate runs the root workspace and both temporary gates; a root-workspace pass alone does not retire a gate.

Run the Go migration input checks from a subshell so later commands remain at the repository root:

```bash
(
  cd runtime
  gofmt -w .
  go test ./...
  go test -race ./...
  go vet ./...
)
```

`gofmt -w` edits source; inspect its diff before committing. Do not claim remote verification from local commands.

## 5. Canonical server development contract

### Commands and process checks

The current canonical process accepts exactly one command argument from this list; it does not currently implement a `version` command, help command, general CLI flags or configuration-file loader. `check-config` validates configuration but does not establish database connectivity, migration compatibility or server readiness. It still requires the mandatory configuration values.

<!-- trnm-server-cli:start -->
`check-config`
`migrate`
`serve`
<!-- trnm-server-cli:end -->

`migrate` runs the selected authoritative schema path. `serve` opens the verified repository before accepting traffic. Do not run migration against an existing or production database as a documentation smoke test.

```bash
python3 scripts/check-rust-server-source-candidate.py
python3 scripts/check-trnm-server.py
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
bash scripts/check-rust-server-process.sh
```

The process smoke builds the canonical binary, validates synthetic configuration and redaction, rejects a missing database URL, then requires startup against an unavailable loopback database to fail within its harness timeout. It does **not** exercise HTTP ingress, authority mutation, duplicate replay or graceful shutdown. Its result retains:

```text
process_ingress_verified=false
graceful_shutdown_verified=false
database_durability_verified=false
compatibility_credit=false
production_ready=false
```

The harness recreates its output directory. Never point `TRNM_SERVER_SMOKE_ROOT` at a directory containing valuable files. Its synthetic password, token and zero schema identity are fixtures, not deployable credentials or a valid migration identity.

The temporary database diagnostic target requires explicit opt-in:

```bash
cargo test --package trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server --locked
```

Live database scripts require their documented environment and immutable images. A missing required profile must fail rather than silently skip. The process smoke cannot substitute for `trnm-server-live`, response-loss, database-profile or immutable-oracle execution.

### Current application dispatch routes

This table documents the bounded HTTP application dispatcher in `crates/trnm-server/src/runtime/app.rs`, not the complete Nakama API and not listener-level WebSocket upgrade routing. The generated gRPC Healthcheck is a separate listener; no general gRPC gateway is implied.

<!-- trnm-server-routes:start -->
| Method | Path | Current scope |
| --- | --- | --- |
| `GET` | `/healthz` | Process liveness response. |
| `GET` | `/readyz` | Current application drain/readiness response; not complete dependency health. |
| `GET` | `/metrics` | Bounded application, session and repository metrics. |
| `GET` | `/v1/session/me` | Candidate access-session verification. |
| `POST` | `/v1/session/refresh` | Candidate refresh-family operation. |
| `POST` | `/v1/session/logout` | Candidate family revocation. |
| `POST` | `/-/drain` | Candidate administrator-authorized drain. |
| `POST` | `/v1/authority/bootstrap` | Candidate administrator-authorized entity bootstrap. |
| `POST` | `/v1/authority/commit` | Candidate administrator-authorized durable command/replay. |
<!-- trnm-server-routes:end -->

A known path with an unsupported method currently returns HTTP 405; an unknown dispatcher path returns 404. These custom routes cannot inflate Nakama parity. Authentication, error precedence, headers and exact response bytes still require their native tests and oracle/profile decisions.

### Current environment configuration reference

The following names are read by the canonical `runtime/config.rs`. Values are read at startup; hot reload and general config-file/CLI precedence remain target work, not an implemented contract. Defaults and bounds below describe configuration parsing; successful parsing does not prove an effective wall-clock bound under stalled I/O. A changed default or inter-field condition requires focused native configuration tests as well as document review.

<!-- trnm-server-config:start -->
| Variable | Current default or requirement | Constraint / handling |
| --- | --- | --- |
| `TRNM_SERVER_BIND` | `127.0.0.1:7350` | Socket address; non-loopback requires explicit opt-in. |
| `TRNM_SERVER_ALLOW_NON_LOOPBACK` | `false` | Candidate-only public bind opt-in; not production authorization. |
| `TRNM_SERVER_GRPC_BIND` | Unset | Optional socket address, different from HTTP; same bind policy. |
| `TRNM_SERVER_DATABASE_URL` | Required | At most 4096 bytes; no ASCII whitespace/control characters; secret-redacted. |
| `TRNM_SERVER_DATABASE_PROFILE` | Required | `postgresql` or `cockroachdb`; evidence remains separate. |
| `TRNM_SERVER_DATABASE_TLS_MODE` | `plaintext-candidate` | Plaintext is refused unless explicitly enabled; `verify-full` selects TLS. |
| `TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE` | `false` | Conflicts with `verify-full`; no production plaintext approval. |
| `TRNM_SERVER_DATABASE_TLS_ROOT_CERT_PEM` | Optional path | Validated nonempty path, at most 4096 bytes, no control characters; mode constraints apply. |
| `TRNM_SERVER_DATABASE_TLS_IDENTITY_CERT_PEM` | Optional path | Must be paired with the identity key. |
| `TRNM_SERVER_DATABASE_TLS_IDENTITY_KEY_PKCS8_PEM` | Optional secret path | Must be paired with the certificate; never print key material. |
| `TRNM_SERVER_SCHEMA_SOURCE_COMMIT` | Required | Exactly 40 lowercase hex characters; schema verification is a separate step. |
| `TRNM_SERVER_ADMIN_TOKEN` | Required candidate credential | 32–512 permitted token bytes; not production RBAC/MFA identity. |
| `TRNM_SERVER_MAX_REQUEST_BYTES` | 131072 | 4096–1048576 bytes. |
| `TRNM_SERVER_READ_TIMEOUT_MS` | 5000 | 100–120000 milliseconds. |
| `TRNM_SERVER_WRITE_TIMEOUT_MS` | 10000 | 100–120000 milliseconds. |
| `TRNM_SERVER_DATABASE_POOL_MAX_SIZE` | 8 | 1–256 connections. |
| `TRNM_SERVER_DATABASE_POOL_MIN_IDLE` | 1 | 0 through configured pool maximum. |
| `TRNM_SERVER_DATABASE_POOL_ACQUIRE_TIMEOUT_MS` | 2000 | 10–120000 milliseconds. |
| `TRNM_SERVER_DATABASE_POOL_IDLE_TIMEOUT_MS` | 60000 | 1000–3600000 milliseconds. |
| `TRNM_SERVER_DATABASE_POOL_MAX_LIFETIME_MS` | 900000 | At least configured idle timeout; at most 86400000 milliseconds. |
| `TRNM_SERVER_DATABASE_STATEMENT_TIMEOUT_MS` | 5000 | 50–600000 milliseconds. |
| `TRNM_SERVER_DATABASE_LOCK_TIMEOUT_MS` | 1000 | 10 through configured statement timeout. |
| `TRNM_SERVER_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS` | 5000 | 50–600000 milliseconds. |
| `TRNM_SERVER_SESSION_AUTH_ENABLED` | `false` | Supplying any session material while disabled is rejected. |
| `TRNM_SERVER_SESSION_AUTH_ISSUER` | Required when enabled | Nonempty, at most 512 bytes; no ASCII whitespace/control characters. |
| `TRNM_SERVER_SESSION_AUTH_AUDIENCE` | Required when enabled | Same bounded profile-text rule as issuer. |
| `TRNM_SERVER_SESSION_AUTH_EPOCH` | Required valid value when enabled | 1–4294967295; zero/default is rejected. |
| `TRNM_SERVER_SESSION_AUTH_KEY_HEX` | Required when enabled | Exactly 64 lowercase hex characters representing 32 secret bytes; development/migration profile only. |
<!-- trnm-server-config:end -->

`engineering_readiness.py` compares the exact command list, route pairs and environment **names** with the recognized source regions. It is deliberately not a Rust syntax/semantic parser and does not establish default values, auth correctness, handler execution or resource safety. A refactor of those source regions requires updating the extractor and its hostile fixtures, not suppressing the check.

### Cancellation lifecycle regression

The pool include root binds exactly `base.rs`, `cancellation.rs`, `pool.rs` and `tests.rs` under `crates/trnm-persistence-pg/src/pool_parts`. Changes to any part, server metrics or the regression harness must trigger the deadline workflow on both pull requests and main pushes.

```bash
python3 scripts/check-pg-operation-deadline.py --self-test
python3 -m unittest discover -s tests/control_plane -p 'test_pg_cancellation_lifecycle.py' -v
cargo test -p trnm-persistence-pg --locked --lib pool::cancellation_lifecycle_tests
cargo test -p trnm-persistence-pg --locked --lib pool::retirement_tests
cargo test -p trnm-persistence-pg --all-targets --locked
```

The Python suite checks source ordering and enumerates a finite callback/cleanup/wire-delivery model. It must find counterexamples for the old behavior, lifecycle locking alone and physical eviction alone, and reject the modeled counterexamples for the combined design. This is not compiled Rust, weak-memory verification, live SQL execution or a replacement for the native tests.

Rust test sources exercise stale callback snapshots, completion waiting for a sender while the registry remains available to other operations, panic/failure accounting, duplicate retirement and actual r2d2 lease eviction. New synchronization regressions use channels and bounded waits rather than assuming a sleeping thread has run.

The live lane sets `TRNM_REQUIRE_LIVE_PG_DEADLINE=1` and provides `TRNM_TEST_DATABASE_URL`. Each exact live test runs with `--lib -- --exact --nocapture`; its log must contain exactly one passing test with zero failures and zero ignored tests before an execution receipt is emitted. A renamed or missing test that produces zero matches must fail the workflow. Both scenarios use a single-connection pool, compare backend PIDs before and after cancellation, and verify a replacement connection can execute `SELECT 1`. The shutdown test waits for the target SQL in `pg_stat_activity`. The deadline test raises the independent statement timeout inside its callback only to distinguish CancelToken behavior from a server-side timeout.

The change does not alter DDL, receipt identity or public error codes. Reverting cancellation hardening would reopen stale-callback and backend-reuse hazards; such a revert is not an approved production rollback. Cancellation transport success must never be recorded as confirmed rollback. Preserve ambiguous-commit reconciliation, distinct PostgreSQL/CockroachDB and TLS evidence, exact-head compilation, independent review and the unresolved stalled-transport deadline requirements.

## 6. Change workflow

[`roadmap/ENGINEERING_EXIT_CONTRACTS.json`](roadmap/ENGINEERING_EXIT_CONTRACTS.json) supplies concrete implementation steps, checks and external obligations for every registered gap, plus the full eleven domain work packages. It is subordinate engineering detail, not an alternate queue or a reduced proof obligation. `NEXT_MILESTONE.json` and the approved architecture overlay still control dependency readiness. No advisory priority can bypass their acceptance conditions. The read-only inventory rejects a missing gap or omitted work package.

1. Identify task, gap, parity leaves and gate impact.
2. Confirm upstream and source identities before changing compatibility behavior.
3. Define error, transaction, concurrency, security, resource and rollback contracts.
4. Implement the smallest vertical behavior with tests in the same change.
5. Run focused checks, then the complete local preflight.
6. Update machine status only to the highest state actually earned.
7. Update the applicable current topic document; do not create a progress snapshot.
8. Push to a non-protected branch and obtain exact-head CI.
9. Retain artifacts and index evidence when the task requires promotion.
10. Obtain the independent reviews required by the gap and CODEOWNERS policy.

A later push invalidates exact-head evidence and stale approvals according to repository policy. Editing a PR body can also require a new metadata-sensitive aggregate without changing the source SHA. Read back live metadata rather than copying predecessor failure counts into the new settlement.

## 7. Rust rules

- `unsafe_code` is forbidden unless an explicitly approved boundary changes the policy.
- `todo!`, `unimplemented!`, warning suppression and ignored mandatory tests are forbidden.
- Public identifiers, generations, revisions, sequence numbers, digests and receipts use strong types.
- All queues, pools, retry loops, batches, parsers and runtime budgets are bounded.
- Errors use stable domain classifications; internal database, token or secret detail is not returned publicly.
- External I/O is never performed inside a mutable database transaction.
- Success is constructed only after commit or exact receipt replay.
- Cloneable wrappers must not clone secret material unnecessarily or expose it through `Debug`.
- A crate may not spawn an untracked global task.

## 8. New crate policy

A new crate requires:

- one stable responsibility and owner;
- documented dependency direction;
- reason it cannot fit an existing boundary;
- public API and resource model;
- unit/property/fuzz/live tests as applicable;
- package-authority registration;
- merge-gate coverage;
- compatibility, gap and evidence mapping;
- removal or convergence plan when it is a temporary gate/prototype.

Crate count, lines of code and commit count are not progress metrics.

## 9. Protocol and persistence development

Protocol adapters own wire details and call services, not repositories. Generated types are pinned to upstream source identities. Hand-written framing or cryptographic code is retained only when its compatibility purpose, test corpus and independent review are explicit.

Persistence code binds the authoritative migration chain, uses serializable transactions where required, repeats revision/generation/lease predicates on every mutation and classifies profile-specific retry behavior. PostgreSQL and CockroachDB conclusions are separate.

## 10. Tests and skips

A developer-only live test may emit an explicit no-credit skip when infrastructure is absent. Required CI sets the required flag and fails on absence. Do not use:

```text
continue-on-error: true
allow_failure
#[ignore]
@unittest.skip
pytest skip markers
conditionals that make a required job empty
```

Any quarantine is time-bounded, owned and cannot close a gate.

## 11. Documentation rules and depth

The live Markdown set under `docs/` is defined by `docs/DOCUMENTATION_AUTHORITY.json`. The only generated human roll-up outside the nine topic documents is `docs/development/FEATURE_PARITY_MATRIX.md`.

Forbidden patterns include date-stamped development notes, `_V1`, `_V2`, `_ALPHA`, `_CANDIDATE`, `_FINAL`, `_SUPERSEDED` and topic-specific `README_*` files. Do not keep redirect stubs; update repository references and delete the obsolete file. Stable subordinate design documents require a reviewed authority/allowlist change; they must not silently create a second current topic.

`MODULE_DOCUMENTATION.json` measures package registration and document presence. The ten required headings and minimum line count are structural checks, not proof of a complete design. `docs/status/DOCUMENTATION_DEPTH.json` is a separate engineering-review proposal covering every registered Rust package and cross-language component. Its depth labels never grant accepted-design, compatibility, durability or production credit.

Each module's existing README must directly specify or link one authoritative contract for these dimensions:

| Dimension | Required engineering content |
| --- | --- |
| Scope and dependencies | Responsibilities, non-goals, callers, dependency direction and named ownership. |
| Public API and examples | Public types/functions/routes, input/output fields, bounds and compilable or executable examples. |
| State and data | State transitions, table/field mappings, invariants and valid/invalid combinations. |
| Errors and side effects | Error precedence, stable classification, mutation-on-error exceptions and retry safety. |
| Concurrency and recovery | Transaction/lock boundaries, idempotency, cancellation, response loss, restart and fencing. |
| Security and budgets | Trust/authorization boundaries, secrets and privacy, numerical resource limits and overload policy. |
| Tests and compatibility | Contract-to-test-to-upstream-leaf mapping, fixtures, profile differences and evidence boundaries. |
| Operations and change | Metrics, alerts, retention, recovery, migration, rollback and version evolution. |

A small pure library can satisfy this with compact tables and examples. A process, database or runtime host requires cross-component sequences and fault cases. Boilerplate, generated API listings and passing this inventory do not replace independent design review. Keep detailed but bounded state-machine design distinct from unimplemented durable/network adapters.

## 12. Pull request requirements

Every PR records scope, owner, task/gap/parity/gate IDs, exact final head/tree, tests, migration/rollback/security effects, evidence or explicit no-credit boundary, residual limitations and forbidden claims. Keep the PR draft while required checks, current identity or P0 findings are unresolved.

The author cannot supply independent acceptance. Automation and generated manifests are not reviewers. A requested source-defect review from an existing account does not prove that account's candidate-specific independence or specialist qualification.
