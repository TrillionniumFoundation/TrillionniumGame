# Development guide

Status: **authoritative current documentation**  
Revision: 2026-09-03

## 1. Development contract

All changes are made against the full Rust reimplementation plan in [`../CURRENT_PLAN.md`](../CURRENT_PLAN.md). A source change is not complete until its tests, machine status, evidence boundary and the applicable current topic document agree.

Do not add a new Markdown file to describe a new iteration of an existing topic. Update the current topic document. Git history, pull requests and issues provide history.

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

The database-backed server candidate is `crates/trnm-persistence-pg/src/bin/trnm-server.rs`. The standalone `crates/trnm-server` executable is a foundation candidate, not a second production authority. New server behavior must move toward the single composition-root architecture described in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 3. Toolchains

The repository pins Rust through `rust-toolchain.toml` and Cargo lockfiles. CI currently uses Rust `1.85.1`. Go toolchain behavior is governed by `runtime/go.mod`; Python control scripts target the runner Python available on Ubuntu 24.04 and use the standard library unless a reviewed dependency is explicitly introduced.

Containerized database evidence uses immutable image digests from `config/database-test-images.json`. Tags alone are not evidence identities.

## 4. Required local preflight

Run the control plane first:

```bash
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tools tests
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Run the root Rust workspace:

```bash
cargo fmt --all -- --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The repository also contains intentionally isolated Cargo workspaces. The aggregate merge gate discovers the package authority and runs format, all-target tests and strict Clippy for every isolated workspace. A root-workspace pass alone is not whole-repository coverage.

Run the Go migration input checks:

```bash
cd runtime
gofmt -w .
go test ./...
go test -race ./...
go vet ./...
```

Do not claim remote verification from local commands.

## 5. Focused server checks

Standalone foundation source and process:

Its CLI exposes `serve`, `check-config`, `version` and help. This is a bounded foundation process contract, not a second production server authority. The request-body ceiling is configured through `TRNM_SERVER_MAX_REQUEST_BYTES`; both CLI and environment inputs remain bounded and are exercised by the source and process contracts.

```bash
python3 scripts/check-rust-server-source-candidate.py
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
bash scripts/check-rust-server-process.sh
```

The process smoke exercises `/healthz`, `/readyz`, `POST /v1/bootstrap`, `POST /v1/command` and exact duplicate replay. Its result explicitly retains:

```text
graceful_shutdown_verified=false
database_durability_verified=false
compatibility_credit=false
```

Canonical database-backed source:

```bash
python3 scripts/check-trnm-server.py
cargo test --package trnm-persistence-pg --bin trnm-server --locked
```

Live database scripts require their documented environment and immutable images. A missing required profile must fail rather than silently skip.

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

The change does not alter DDL, receipt identity or public error codes. Reverting it would reopen stale-callback and backend-reuse hazards; such a revert is not an approved production rollback. Cancellation transport success must never be recorded as confirmed rollback. Preserve ambiguous-commit reconciliation, distinct PostgreSQL/CockroachDB and TLS evidence, exact-head compilation, independent review and the unresolved stalled-transport deadline requirements.

## 6. Change workflow

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

A later push invalidates exact-head evidence and stale approvals according to repository policy.

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

## 11. Documentation rules

The live Markdown set under `docs/` is defined by `docs/DOCUMENTATION_AUTHORITY.json`. The only generated human roll-up outside the nine topic documents is `docs/development/FEATURE_PARITY_MATRIX.md`.

Forbidden patterns include date-stamped development notes, `_V1`, `_V2`, `_ALPHA`, `_CANDIDATE`, `_FINAL`, `_SUPERSEDED` and topic-specific `README_*` files. Do not keep redirect stubs; update repository references and delete the obsolete file.

## 12. Pull request requirements

Every PR records scope, owner, task/gap/parity/gate IDs, exact final head/tree, tests, migration/rollback/security effects, evidence or explicit no-credit boundary, residual limitations and forbidden claims. Keep the PR draft while required checks, current identity or P0 findings are unresolved.

The author cannot supply independent acceptance. Automation and generated manifests are not reviewers.
