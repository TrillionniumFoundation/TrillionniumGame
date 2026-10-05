# Development guide

Status: **authoritative current documentation**  
Revision: 2026-10-05


The canonical App now has one selected `AuthAuthorityRuntime`: disabled, durable-family, or Nakama legacy. Server startup verifies the selected schema/catalog/import state before constructing one Legacy service or owned durable verifier, then shares the selected keys/cache across workers. The four Legacy POST routes and storage Access context are source candidates; Legacy never uses durable session-family verification/rotation/revocation. `check-config` constructs no service, pool or worker, default schema remains StorageV4/epoch4, and the AccountsV5 capture gate remains false before account SQL or listeners. HTTP credential/query/body/response Debug is redacted and unauthenticated Legacy errors install a bounded static WWW-Authenticate header. Partial startup errors use the same drain/cancellation/join cleanup as normal shutdown. Exact native HTTP, paired oracle and production lifecycle qualification remain pending.


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
| `GET` | `/` | Public empty root probe; query ignored; bounded special-route header profile. |
| `GET` | `/healthcheck` | Public Nakama health source route; query ignored, empty JSON; bounded header profile. |
| `GET` | `/healthz` | Process liveness response. |
| `GET` | `/readyz` | Current application drain/readiness response; not complete dependency health. |
| `GET` | `/metrics` | Bounded application, session and repository metrics. |
| `GET` | `/v1/session/me` | Candidate access-session verification. |
| `POST` | `/v1/session/refresh` | Candidate refresh-family operation. |
| `POST` | `/v1/session/logout` | Candidate family revocation. |
| `POST` | `/-/drain` | Candidate administrator-authorized drain. |
| `POST` | `/v1/authority/bootstrap` | Candidate administrator-authorized entity bootstrap. |
| `POST` | `/v1/authority/commit` | Candidate administrator-authorized durable command/replay. |
| `PUT` | `/v2/storage` | Bounded candidate-session storage write batch; genuine database timestamps; complete parity remains open. |
| `POST` | `/v2/storage` | Bounded candidate-session read batch; missing/hidden objects omitted, genuine timestamps projected to seconds. |
| `PUT` | `/v2/storage/delete` | Bounded candidate-session storage delete batch. |
| `GET` | `/v2/storage/{collection}` | Nakama client-profile public list with unsigned gob position and authenticated SQL ACL. |
| `GET` | `/v2/storage/{collection}/{user_id}` | Nakama client-profile own/foreign/global owner list; genuine timestamps projected to seconds; exact differential remains open. |
| `POST` | `/v2/account/authenticate/custom` | Capture-gated Custom authentication; same selected server-key authority |
| `POST` | `/v2/account/authenticate/device` | Legacy source route, including query variants; fixed Basic server key before complete business decode and account access; AccountsV5 gate closed. |
| `POST` | `/v2/account/session/refresh` | Legacy source route; fixed Basic server key, no durable-family rotate or fallback; AccountsV5 gate closed. |
| `POST` | `/v2/session/logout` | Legacy source route; AccessBearer before body decode and blacklist mutation; AccountsV5 gate closed. |
<!-- trnm-server-routes:end -->

For `/healthcheck`, ordinary unsupported methods return HTTP 501/code 12; healthcheck-only form handling is described below; wider form routing and OPTIONS/CORS parity remain open. Other installed Nakama client paths use the bounded gateway method error below; operator paths with an unsupported method retain HTTP 405, and an unknown dispatcher path retains 404. These custom routes cannot inflate Nakama parity. Authentication, error precedence, headers and exact response bytes still require their native tests and oracle/profile decisions.

The canonical process also has a public GET `/` source probe with an empty response and `Content-Length: 0`. It ignores query parameters and credentials, performs no repository access, and omits Content-Type, Cache-Control and Vary, matching the pinned special-route header subset. Only this installed root target is added to the closed CORS allowlist. Nine actual loopback cases execute the original root registration and Gorilla CORS policy; raw Date headers are retained rather than normalized away. Existing startup verification, request/header bounds, framing, deadlines, operator authentication and drain restrictions remain unchanged. Root methods other than GET keep their previous native errors; redirect/encoded-path behavior, Date, connection headers, candidate metrics and complete lifecycle equivalence remain open. `contracts/http/nakama-root-probe-v1.json` binds the scope, tests and rollback; no gap, gate or compatibility claim is promoted.

The canonical HTTP dispatcher now includes a public GET `/healthcheck` source candidate with exact `{}` success bytes and the pinned JSON/cache/Vary/gRPC-metadata header values. It ignores query parameters and does not consult session or repository authority. Health remains a liveness signal while `/readyz` rejects incomplete import or drain; verified startup admission and listener shutdown are unchanged. Ordinary unsupported methods use HTTP 501/code 12, and HEAD suppresses the wire body while retaining its response length. The bounded contract and native fixtures are in `contracts/http/nakama-healthcheck-v1.json`.

The bounded Nakama client CORS adapter now follows the pinned fixed-origin policy only for installed `/`, `/healthcheck`, storage and four Legacy HTTP targets. Preflight completes before application/auth/repository dispatch, while actual requests keep their original method and authentication checks. The wildcard response never includes credential permission; header values come only from a closed canonical name set. Operator/control routes, readiness, metrics, internal realtime and unimplemented routes are excluded. The policy, 22 original-source middleware fixtures, seven native regressions and remaining transport differences are recorded in `contracts/http/nakama-cors-v1.json`. No configuration flag, persistent permission, schema or writer change is involved.

The installed storage and Legacy HTTP targets now reject ordinary unsupported methods with the pinned gateway HTTP 501/code 12 envelope and `Method Not Allowed` message, including benign query strings. HEAD retains the error content length and suppresses the wire body. Valid GET `/v2/storage/delete` still selects collection `delete`; actual valid-method authentication, mutating admission, operator errors and unknown targets are unchanged. `contracts/http/nakama-routing-v1.json` binds 27 generated-gateway source cases and three native regressions. The reproducible reference runner verifies the frozen upstream files and Go 1.26.5 before invoking the original generated gateway. Form-POST fallback, method override, encoded-path/error ordering and full immutable-image/header qualification remain open. This source candidate changes no schema or writer and grants no acceptance; rollback reverts the scoped dispatcher and source bindings.

Healthcheck also has a bounded form-POST source adapter: the exact `application/x-www-form-urlencoded` media type enables GET fallback, after original form validation and method-override ordering. Body errors precede query errors, literal semicolons override escape errors within one form, and malformed-byte quoting matches a 65,536-case Go 1.26.5 reference digest without lossy UTF-8 or Unicode normalization. Forty-eight pinned generated-gateway cases cover media types, overrides and parse errors. Only the public health target is affected; storage/Legacy/operator methods and existing request/header limits, framing, deadlines and transport drain denial are preserved. An overridden HEAD suppresses the error body while keeping its length. The original keepalive source probe made a POST client wait for its bounded read deadline in that case; the candidate still closes the connection, so complete lifecycle equivalence is explicitly unaccepted. `contracts/http/nakama-healthcheck-form-v1.json` records source identities, tests and rollback. No source presence or local result grants a gap, compatibility or production claim.

The 17-case source-derived gateway diagnostic is not the immutable Nakama process. Date, connection/header differences, wider form-POST routing, OPTIONS/CORS qualification, configured headers and compression remain explicit residuals. No compatibility, gap, production or replacement claim is promoted. This change has no migration impact; rollback reverts the route and its bindings together and restores the prior `/healthcheck` 404.

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
| `TRNM_SERVER_SCHEMA_TARGET` | `storage-v4` | Only `storage-v4` or `nakama-accounts-v5`; immutable across pool leases. AccountsV5 remains capture-gated before DB I/O or Legacy runtime/listener construction; App source routes alone do not activate it. |
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
| `TRNM_SERVER_AUTH_MODE` | Unset selects the old explicit enablement rule | Exactly `disabled`, `durable-family` or `nakama-legacy`; unknown, mixed and partial profiles reject. With no materials the authority is Disabled. |
| `TRNM_SERVER_SESSION_AUTH_ENABLED` | `false` when mode is absent | Old `true` requires all four Durable fields. Explicit durable-family permits an absent flag and rejects false; disabled rejects true or any material; Legacy rejects even a leftover false flag. |
| `TRNM_SERVER_SESSION_AUTH_ISSUER` | Required when enabled | Nonempty, at most 512 bytes; no ASCII whitespace/control characters. |
| `TRNM_SERVER_SESSION_AUTH_AUDIENCE` | Required when enabled | Same bounded profile-text rule as issuer. |
| `TRNM_SERVER_SESSION_AUTH_EPOCH` | Required valid value when enabled | 1–4294967295; zero/default is rejected. |
| `TRNM_SERVER_SESSION_AUTH_KEY_HEX` | Required when enabled | Exactly 64 lowercase hex characters representing 32 secret bytes; development/migration profile only. |
| `TRNM_SERVER_LEGACY_SERVER_KEY` | Required in explicit nakama-legacy mode | Exact untrimmed UTF-8 bytes, 1–4096 bytes; no public/default key. |
| `TRNM_SERVER_LEGACY_ACCESS_KEY` | Required in explicit nakama-legacy mode | Exact 1–4096 bytes; no Durable 32-byte minimum or implicit decoding. |
| `TRNM_SERVER_LEGACY_REFRESH_KEY` | Required in explicit nakama-legacy mode | Exact 1–4096 bytes; equal access/refresh values reject under inherited local policy, an explicit parity residual. |
| `TRNM_SERVER_LEGACY_ACCESS_TTL_SECONDS` | Explicit positive integer required | Positive i64 with actual local duration checks; maximum 4611686018 seconds, no implicit TTL. |
| `TRNM_SERVER_LEGACY_REFRESH_TTL_SECONDS` | Explicit positive integer required | Positive i64 with actual local duration checks; maximum 9223372036 seconds, no implicit TTL. |
| `TRNM_SERVER_LEGACY_SINGLE_SESSION` | `false` | Exactly true/false/1/0; in Legacy mode only. |
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

The canonical storage HTTP API candidate has native codec, application and
retry-wrapper regressions:

```bash
cargo test -p trnm-server --locked storage_api
cargo test -p trnm-server --locked storage_list
cargo test -p trnm-server --locked storage_cursor
cargo test -p trnm-server --locked storage_write_and_delete_are_never_automatically_retried
cargo test -p trnm-server --lib --locked runtime::storage_api_tests::canonical_storage_api_live_database -- --exact --nocapture --test-threads=1
cargo test -p trnm-persistence-pg --test authority_storage --locked nakama_client_listing_modes_cursors_and_integrity_are_database_projected -- --exact --nocapture --test-threads=1
```

They exercise original value bytes, default/zero permissions, duplicate known
fields and aliases, exact integer parsing, empty batches, ownership, persisted
revocation, authentication precedence, atomic failure, drain and retry
suppression. Read regressions cover global/default owners, pinned UUID forms,
hidden/missing objects and defensive response validation. Exact version inputs
retain uppercase, nonhexadecimal, Unicode and long strings through the API and
repository. They must reach ordinary OCC and permission evaluation rather than
an early MD5-format rejection. Write empty/star mapping differs from delete,
where `*` is an exact condition. Prefixes and suffixes must not be truncated or
normalized. The native JSONB path also retains historical opaque stored versions and checks
profile-specific NUL behavior; these source regressions do not qualify the
immutable Nakama differential. Mock repositories and the real storage state model provide local
feedback; live database and immutable-oracle evidence remain separate. Read
`contracts/storage/nakama-http-storage-v1.json` and the open
`DIV-STORAGE-HTTP-*` records before changing these endpoints. Schema v2 appends nullable storage timestamps and a shared migration engine.
Original v1 times stay unknown unless separately carried by a source-verified
transfer; schema 4 does not invent a timestamp backfill. Reverting API source does not authorize reverting
the writer epoch, schema or storage privilege barrier.

The last command requires `TRNM_DATABASE_URL`, `TRNM_DATABASE_PROFILE` and
`TRNM_REQUIRE_LIVE_DATABASE=1` to prove actual database execution. The existing
live harness runs this canonical Rust application fixture for each database
profile and rejects zero tests, optional skips and missing execution markers.
It uses dedicated fixture rows and verifies persisted effects and revocation. The same fixture must emit its original-condition marker after all 15 write, 18 delete and two rollback cases have passed; the native harness checks the exact profile and counts before sealing the log. The marker starts on its own line even when the single-threaded Rust test harness writes its test-name prefix first; local reproduction uses the same `--test-threads=1` and whole-line check as CI.
Independent SQL sentinels verify that a blind unchanged value/ACL preserves
legacy update time while an exact-version write still refreshes it. The live harness also requires the exact repository ACL/OCC fixture once per
profile, retaining its log and rejecting empty or skipped execution. It checks
insert-only version conflicts separately from blind/exact permission rejection,
prior-write rollback and unchanged persisted values, ACLs and both timestamps.
The timestamp fixtures also check persisted database times; populated historical
NULL recovery, trusted source custody and the immutable upstream differential
remain explicit gaps.
This exercises the HTTP application and repository together; TCP ingress,
pooled deadlines, SDK and immutable Nakama differential remain separate checks.

The two GET list templates are owned by `STORAGE_LIST_ROUTES` and dispatched
through the live list matcher. The read-only engineering inventory extracts that
constant and the guarded dispatcher when the production list module is integrated;
unreachable literal arms and test-only constants do not establish routes.
List limits default to one and accept 1–100. Omitted owner lists public objects
across owners with read >= 2; own owner lists read >= 1; foreign or explicit zero
owner lists read == 2. Batch read separately permits read == 2 or owner/read == 1;
client write requires write == 1 while client delete accepts write > 0. One readonly serializable SQL query applies ACL before
its `limit + 1` sentinel and uses database text ordering. It decodes returned rows
only, so hidden and sentinel integrity failures do not affect earlier pages.

The Nakama list profile uses an unsigned URL-base64/gob position containing key,
owner UUID and read permission. This is an explicit exception to the internal
typed list's authenticated scope-bound cursor requirement: every request verifies
the principal, and SQL applies its ACL independently of the offset. Cursor fields
ignored by each upstream mode supply no authorization. The candidate accepts
arbitrary int32 read offsets and empty keys under a 4096-byte key budget.
Collection queries accept empty/dot/control text up to 4096 UTF-8 bytes. Returned
rows use a separate Nakama stored-key projection matching the authoritative 0–128 Unicode
character constraints, including empty/dot/control text; the old typed API keeps its
byte bounds. Request targets, gob streams, types, fields, nesting and container
work have explicit budgets in the storage contract. Exact gateway alias behavior,
non-UTF8 Go strings, unsupported gob descriptors, collation, concurrent-page
behavior, timestamps, official SDK and immutable-oracle evidence remain open.

The repository list fixture requires the live environment above and emits
`nakama_client_list_projection_executed profile=...` only after assertions and
scoped cleanup. It independently seeds Unicode/dot/control rows and damages
visible, hidden and sentinel digests. Source presence and local execution do not
admit a live packet or establish compatibility.

### Storage transfer development

The current chain is 0001/0002/0003/0004 per profile, schema 4/writer epoch 4 and twelve authoritative tables. Keep historical SQL and the existing six schema-upgrade regressions plus the 41 v3 observations across shapes, illegal legacy data, catalog drift, partial resume, metadata validation and opaque history. V4 source/import tests extend this coverage; they cannot replace those families or turn a source-DDL fixture into an immutable Nakama oracle.

The operational binaries are `trnm-storage-export` and `trnm-storage-import`; their shared argument/environment parser is `crates/trnm-persistence-pg/src/bin/storage_transfer.rs`. Export accepts `snapshot PACKET_DIRECTORY --candidate-plaintext` and requires a new directory. Import accepts `verify-packet`, `preflight`, `begin`, `apply-page`, `apply`, `resume`, `verify-applied` or `finish`, followed by the packet directory. Only `verify-packet` is offline and takes no plaintext flag; database modes require `--candidate-plaintext`. `apply` registers or reconciles the proven prefix, applies remaining pages and finalizes. `resume` and `verify-applied` are read-only; `finish` cannot complete missing pages. Refer to the binary's `--help` for the exact current argument contract.

| Environment group | Required inputs |
| --- | --- |
| Database modes | `TRNM_DATABASE_URL`, `TRNM_DATABASE_PROFILE` (`postgresql` or `cockroachdb`). |
| Producer/custody identity | `TRNM_STORAGE_PRODUCER_COMMIT`, `TRNM_STORAGE_PRODUCER_TREE`, `TRNM_STORAGE_PRODUCER_SOURCE_SHA256`, `TRNM_STORAGE_PRODUCER_BINARY_SHA256`, `TRNM_STORAGE_EXECUTION_ID`. |
| Export snapshot | `TRNM_STORAGE_EXPORT_PAGE_ROWS`, `TRNM_STORAGE_SOURCE_EXECUTION_CLASS`, `TRNM_STORAGE_PRODUCER_SOURCE_FILE`, `TRNM_STORAGE_UPSTREAM_DIRECTORY`. |
| Every import mode | Independent `TRNM_STORAGE_EXPECTED_MANIFEST_SHA256` and `TRNM_STORAGE_EXPECTED_RECEIPT_SHA256`, plus producer/custody identity above. |
| Import database modes | `TRNM_STORAGE_IMPORT_AUDIT_AT_MS`, `TRNM_STORAGE_LEGACY_WRITER_ROLE`, independent `TRNM_STORAGE_EXPECTED_TARGET_SCOPE_SHA256`. |

Producer commit/tree must be lowercase nonzero 40-hex identities and the source/binary anchors are SHA256. The exporter reads actual `exporter.rs` and the executing image, plus the exact pinned initial SQL/core-storage/LICENSE bytes. Do not derive trust inputs from untrusted packet fields. Values are native JSONB text in separate files, not reserialized serde/f64 payloads, and the packet's manifest and external receipt are independently anchored. Linux held descriptors reject symlink substitution during packet verification. Candidate plaintext transport and local source-DDL execution do not implement production issuer/signature trust or prove a clean Git build.

Keep separate budgets: 256 MiB packet, 10,000 rows, at most 100 pages/100 rows per page, 16 MiB value, 32 MiB summed native-value bytes per page and 2 MiB manifest; row metadata is capped at 16 MiB. The export snapshot and database import sequence have a 300-second operation budget with statements capped at five seconds/remaining time. Source/target collation bindings must match: PostgreSQL supports UTF8/libc and actual deterministic default key collations, CockroachDB uncollated keys. Its target identity classification explicitly reports unobserved physical cluster identity. Native roundtrips must preserve time precision, public versions, all lawful JSONB shapes and stored 0–128 Unicode keys/nonnegative SMALLINT ACLs. New request validation is a separate boundary. SQL materialization memory and stalled synchronous I/O remain unresolved resource limits.

Read `docs/OPERATIONS_AND_RELEASE.md` before running any mutable transfer against an existing target. Tests must cover whole preflight rejection without job/data changes, page rollback, restart/exact resume, conflicting receipts, final inventory and incomplete-import admission. Preserve the full denominator, all existing gates and false compatibility/production/replacement flags.

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

`scripts/ci-trnm-server-live.sh` records stages before configuration, migration,
required native suites and process assertions. A failed stage invokes
`scripts/print-server-live-failure.py` with public stage metadata and at most two
known log paths. Credentials pass through the environment. The helper emits a
single redacted JSON record capped at 1 MiB, reads at most 64 KiB per regular log,
discards partial first and unterminated last lines and keeps at most 80 lines. Missing logs, symlinks
and FIFOs are not followed. The original shell failure remains the exit status
even if the helper fails. Configuration leak checks use quiet grep so the check
cannot echo the leaked credential. Run
`python3 -m unittest tests.control_plane.test_storage_list_live_contract -v`
for real shell-trap, redaction, resource-bound and unchanged success checks.
Failure diagnostics do not authorize a rerun or satisfy a successful evidence
packet; use the actual error to select the correction.
Both Rust process paths keep their existing CLI success messages and public
error mappings. A failed migration emits bounded operator JSON for pool setup,
session acquisition or schema application. Its reason comes from a closed
allowlist; unknown reasons, private I/O paths and credentials are omitted.
The domain adapter has already discarded raw SQLSTATE, so that field is null
rather than reconstructed from a generic error code.

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

The timestamp schema lifecycle fixtures run with `cargo test -p trnm-persistence-pg --locked --test schema_upgrade -- --nocapture --test-threads=1`. Set the dedicated `TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL`, `TRNM_DATABASE_PROFILE` and `TRNM_REQUIRE_LIVE_DATABASE=1`; the admin URL never falls back to the ordinary database URL. Tests create isolated databases and synthetic roles, cover fresh/repeated/readonly verification, original provenance and unknown history, old-session write rejection and catalog/identity drift, then clean up their own resources. Separate PostgreSQL and CockroachDB executions are required. A success in local fixtures is source validation, not migration or production acceptance.

The embedded registry records each reviewed revision's migration-prefix digest,
explicit writer epoch and half-open range in the append-only action inventory.
Historical v1, v2 and v3 digests remain distinct and immutable; unknown revisions
are rejected. Publication compares all previous metadata fields with NULL-safe
predicates, preserves foundation provenance and counts the revisions actually
published. A historical digest does not grant current serve readiness. The engine supports the locked four-file v1-to-v2-to-v3-to-v4 chain, typed bounded native JSONB conversion and source-transfer journal schema. Historical opaque versions and exported times now have a connected bounded transfer path; production source custody and populated NULL-history repair remain separate work. An older revision is not ready to serve.

Backup/restore and Cockroach retry CI retain actual execution packets through the repository's local upload action. Packets bind the source commit/tree, workflow/run/attempt/job/profile, complete profile SQL chain and real schema reports; retry captures its fresh and read-only reports before deleting its owned test database. Shared archive checks enforce 512 retained entries, 32 MiB payload and 2 MiB compressed/per-file diagnostic limits, then the workflow verifies the uploaded artifact ID, byte count and SHA. These bounded CI fixtures do not qualify production backup volume, multi-node recovery or independent acceptance.

The storage v3 regression targets include `trnm-storage-core` projected model tests, `trnm-persistence-pg --test storage_jsonb`, existing authority/timestamp fixtures, the six isolated schema lifecycle tests and the canonical application fixture. The model's default projector is explicitly identity bytes; only actual native profile runs establish JSONB projection. Preserve existing execution markers and condition matrices, then require the independent `storage_jsonb_v3_live_executed` application marker. All four current profile SQL files, exact native schema 4/epoch 4 reports and actual prior-v2/prior-v3 publisher provenance belong in source/prospective, backup, restore, retry and producer archive identity checks. The six original upgrade regressions and 41 v3 observations remain separate required coverage; no new transfer fixture substitutes for them. The live harness passes absolute upstream and import-packet directories to Cargo integration tests, whose working directory is the persistence crate; retained archive paths remain unchanged. Schema-4 operational snapshots include all twelve tables, with native deterministic key orders for both import journals. Schema verification recognizes only the two captured full PostgreSQL 17.6 serializations of the import jobs-count and pages-bound CHECK constraints. Tests reject altered bounds, predicates, constraint keys, profile, validation state and arbitrary formatting; no general expression normalizer is used. Retain the source and restored catalog observations separately.

The live storage harness additionally runs exactly one `storage_duplicate_batches` integration test on each profile after the existing v4 import lane, using required database/profile, admin URL, absolute pinned source annex and actual candidate commit/tree inputs. Its original seven unique whole-line markers still bind late invalid-JSON rejection, complete fifteen-field success, the thirteen-occurrence original-ACK/final-ordinal case, a separate three-row/three-page source-DDL export/import history case, an earlier missing delete rejecting while a later row remains locked, two held-write-tail/three early-stop cases with fifteen-field rollback, and final completion. The same canonical App fixture adds `storage_homogeneous_app_executed` with thirteen write occurrences and three rollback cases. The added `nakama_native_jsonb_exact_matrix_executed` binds eleven main cases and `nakama_native_jsonb_exact_subvector_executed` separately binds the legal surrogate input. The authenticated App also requires one `storage_jsonb_native_write_failure_executed` marker with one case, fifteen fields and the independent native-expression SQLSTATE22P02. Its authenticated App assertions are exact HTTP500, rollback and same-connection read; they do not expose or infer the App native SQLSTATE. Each added marker must occur once for the packet profile; an older seven-marker log cannot qualify this source. Both logs must have one successful test, zero failed/ignored and no optional skip before summary/sealing. The original eight-row/four-page/nine-JSON import fixture, six schema tests plus 41 v3 observations and 15/18/2 condition matrix stay required. Synthetic Bash and archived-log regressions verify the guards; they are not native database execution.

Homogeneous Nakama HTTP write/delete batches use a separate canonical-owner policy that preserves each occurrence. The bounded Go 1.26.5 `sort.Sort` translation orders execution by collection/key/canonical owner without an ordinal tie; write ACKs retain original input positions. Every occurrence refreshes the transaction's current ACL, version and native row, and a later rejection rolls back every earlier effect. Client missing/conditional deletes reject the batch; only an unconditional server delete can treat a missing row as a no-op. The internal heterogeneous batch and batch-read policies retain duplicate-key rejection. Maximum 100 occurrences, raw Runtime owner-string order, hooks/index positional effects, native SQL lock/error timing, retries and SDK/immutable-oracle qualification remain separate open boundaries. The BSD-derived sorter retains its license, both source copyright years and the exact source lock; these source bindings grant no acceptance. The narrow SomeWrite candidate binds original request bytes as native JSONB with FormatText, and Exact conditions as raw native TEXT, before per-occurrence host ACL/OCC classification. Exact acquisition and UPDATE both predicate on the literal token and server-or-write1 eligibility; an excluded row uses a nonlocking skinny fallback, and an unexpectedly eligible fallback fails closed. Any occurrences first execute an honest native fifteen-column INSERT with ON CONFLICT DO UPDATE WHERE FALSE, using the original request bytes and a native UTF8 SHA256 projection digest. One returned row proves an actual insert; only that branch produces an absent previous receipt. A zero-row conflict requires the real current prior row. PostgreSQL uses the retained conflict lock; CockroachDB takes an explicit skinny ACL FOR UPDATE lock and, after authority succeeds, reads the complete prior under FOR UPDATE because its reservation does not retain that lock. A matching token/read/write tuple preserves all fifteen old fields, including unknown provenance and microsecond times; other allowed writes use a prior-bound native upsert. The staging map supplies budgets, never prior-row authority. Validated nonempty all-Any SomeWrite batches use explicit READ COMMITTED only on PostgreSQL; mixed conditions, typed batches, deletes and every CockroachDB batch remain SERIALIZABLE. No mutation is implicitly retried. These source policies and controlled SQL prerequisites do not qualify production algorithm, source HTTP equivalence or the complete concurrency schedule. SomeWrite insert-only occurrences bypass existing-row acquisition and full-row loading, bind original JSONB bytes in a bounded native projection and execute a plain INSERT. A staged None is only insertion input, not evidence that the database key is absent. The INSERT unique failure remains a hard stop with the existing version rejection; other native failures retain their classification and the first earlier semantic rejection still wins. The finite insert-only matrix requires three cases: an Any positive wait, a committed-existing star and an uncommitted-delete star positive wait followed by holder rollback. Committed-existing completion/wait counts are PostgreSQL 1/0 and CockroachDB 0/1; the deletion case requires a real plain-INSERT waiter in both profiles. Every case retains fifteen-field rollback, no ACK and same-lease readback. The extra native projection, database-internal rendering limits, full pgx preparation/grouping, isolation/retries and concurrent winner/race behavior remain unqualified. Hidden batch SQLSTATE is not reconstructed from a public version rejection. The first host ACL rejection or Exact mismatch is remembered while real later work continues until a hard error; existing insert-only conflict, native error, DataLoss or resource rejection stops further SQL. Explicit rollback precedes the inner error return. Failed rollback remains unconfirmed and retires a pooled lease only to prevent recycling, without disabling direct repositories. Outer pool deadline/shutdown may override the inner primary error. Native Write InvalidArgument with the exact database_constraint_violation reason maps to HTTP500/code13; host validation, ACL/OCC and delete mappings retain their separate behavior. The old seven fixture markers and App13/3 remain required. The added native matrix has eleven main cases plus a separately bound legal-surrogate subvector; the same fixture binds an independently observed native-expression SQLSTATE22P02 to authenticated App HTTP500, exact fifteen-field rollback and a same-connection read; the App's hidden SQLSTATE is not reconstructed. The historical held-invalid-b observation belongs to its earlier source; the new causal case uses valid held b then invalid c before held z. Controlled official PostgreSQL HTTP observations motivated this candidate but do not qualify its execution. The two late Exact exclusions mean conditional eligibility exclusion, not absence of native waiting. The required matrix binds late_exact_wait=0/late_exact_no_wait=2 for PostgreSQL and 2/0 for CockroachDB; the sealed summary must independently contain matching integer late_exact_wait_cases and late_exact_no_wait_cases. This finite profile difference follows controlled official-source primary-index causal observations with a positive Any wait, literal single Exact and early ACL/late Exact requests; old secondary-index observations and the original rejected query classifier remain separate. It changes no production SQL or isolation policy and grants no full native lock schedule, oracle acceptance or replacement claim. In the separate literal-NUL Exact condition case, PostgreSQL's native TEXT rejection stops the tail, while CockroachDB accepts the TEXT input, preserves the first ACL rejection and waits on a later Any write. That later Any query follows the profile-specific Any reservation/acquisition path; it is not a third late Exact exclusion, so the 0/2 and 2/0 late Exact counters remain unchanged. The source observation binds the later Any's actual primary-index waiter/holder join and HTTP first ACL error; it does not reveal a hidden SQLSTATE for the first NUL condition. The fixture policy is checked in its local waits body, Exact query selector and three actual profile-aware call sites. These static guards do not prove candidate native execution. Full pgx prequeue/preparation/query-group and parameter schedule, native ON CONFLICT/insert-only lock behavior, concurrent Exact predicate races, native isolation/retries, raw Runtime owner ordering, hooks/index, SDK/immutable-oracle and independent acceptance remain open. Input/projection/response bounds do not prove database-internal JSONB memory containment. No error, identity, ACL, version, occurrence or durable effect is normalized; all full compatibility and replacement claims remain false.

The next account schema is an isolated source frontier: locked `0005_nakama_accounts_up.sql` declares the current Nakama 3.40 `users` and `user_device` fields, defaults and constraints, including both added provider IDs and the Console device preferences/push-token fields. Schema version and storage writer authority are separate: the complete embedded source chain reaches five files, while the default canonical serve/migrate profile retains the exact schema-4 prefix, twelve tables and storage writer epoch 4. `AuthoritativeSchemaTarget::NakamaAccountsV5` is explicit and rejects before namespace reads or DDL with `schema5_native_catalog_capture_pending`; no native default, constraint or index rendering has been invented. Root must bind exact PostgreSQL and CockroachDB catalog observations, qualify the adjacent migration and retain the real schema-4 publisher before activation. An account table installed outside that path is rejected by default readiness. The storage packet, its schema-4 guard framing and import/export API do not adopt account data or schema 5. The typed account reader, bounded Device transaction and concrete server adapter are source candidates behind this closed gate. Account HTTP/gRPC routes and startup configuration, account transfer, the display-name search/index profile, schema-5 migration/backup/restoration qualification and independent acceptance remain unfinished. Existing storage and migration regressions keep their schema-4 profile; source frontier identity is not a runtime migration or compatibility claim.

The complete frontier5 source and selected runtime4 identity now have separate typed source/execution-prefix contracts retaining all five source-file proofs and the original four-file storage execution identity. This source connection does not qualify a current-HEAD live-storage, backup, retry or outbox packet. Existing execution guards, the immutable roadmap, gap scope and full compatibility denominator remain in force; schema5 activation stays closed.

完整 authoritative source frontier 为 schema5 时，默认 StorageV4 仍只执行原始连续四步，使用原 digest4、12 表和 writer epoch4。控制面通过共享 issuer token 接入运行身份与归档校验；`schema-source-selection.json`、`schema-source-head.json` 和 `full-schema-source/` 分别保留完整源证明、生产者 Git 对象校验上下文和两 profile 全部十份 SQL。新 sidecar 的封闭 18 文件清单包含真实 lock5、原始 validator、共享 kernel、调用绑定工具与 image 配置，不能构造 current lock4 或把第五步截掉后称作完整链。归档的四步执行清单与 storage4 导入导出协议继续独立验证。

`NakamaAccountsV5` 仍在来源或数据库 I/O 前拒绝，账号运行、账号传输和 schema5 备份恢复未获得资格。源 kernel 不授予 Git、数据库执行或独立 acceptance；真实 2005 条目录负例只验证源枚举采用有界流式 `os.scandir`，非 `.sql` FIFO 不读取也不构成整个命名空间的常规文件保证。历史 source4 只能使用显式历史 envelope 与真实原 validator、authority、八份 SQL，不能冒充当前源。新调用接入的 native、remote 和 Git fixture 验证仍由独立执行矩阵负责。


Legacy authentication now has an owned source composition in `runtime/legacy_auth.rs`, the concrete `legacy_repository.rs` adapter, and the distinct issuer/header/claims/verifier/blacklist/device-predicate modules. `python3 scripts/check-trnm-server.py` checks their finite source regions, registrations, deferred UUID/clock/error ordering, fixed-purpose references and truthful no-credit status; `tests/control_plane/test_nakama_accounts_schema5.py` includes mutation negatives. These checks are source contracts, not Cargo, HTTP or native authentication execution. The first-party crypto-provider dependency edges remain explicit in the existing closed dependency table; source registration adds no external version, SDK, key fallback or default gate. Four Legacy routes are now registered separately as capture-gated source wiring. Exactly one selected authority now owns App authentication: Disabled, DurableFamily or NakamaLegacy. This source selection does not activate the false AccountsV5 gate or grant native HTTP execution.

The new Device error envelope distinguishes `CommittedCreation`, `Unconfirmed` and `UnconfirmedCleanup`; false confirmation is not proof of no commit. Preserve the original cause/code and cleanup diagnostic, permit neither caller replay nor compensation, and leave native transaction attempts to the repository. Stored username/disable timestamps are account read data, not freshly validated Device input. Treat the relation catalog observations, pure predicate/codec comparisons, local service mocks and official-only HTTP observations as separate finite records. Root's combined-source partial Cargo checks do not establish this full source or native Device qualification. The current source frontier is five, but default execution remains four/epoch4/twelve tables until exact native activation is independently qualified.

The shared source-fetch kernel checks its 30-second request and 605-second collection budgets before I/O and after delivery, including late successful delivery. The source worker's finite 199 regressions and independent late-delivery repair checks cover these policy paths only. Nonreturning platform/kernel I/O and a global hard teardown guarantee remain open; no previous-head CI packet is rebound to this source. Retain original failed attempts rather than promoting them after a repaired candidate.

The inactive target/pool/HTTP composition is checked against complete raw production SHA bindings before finite source contracts. `check-trnm-server.py` reads the actual target-carrying direct/pool constructors, typed single-lease call paths, result envelopes and HTTP codec. The closed foundation dependency policy permits only the server's existing locked `base64 = "=0.22.1"` direct edge; persistence and protobuf build boundaries remain separate. Mutation tests reject target fallback, gate removal, repeated native calls, lost late-commit/cleanup observations, recycled canceled leases, registry/codec drift, coerced budgets and overstated flags. These checks neither parse arbitrary Rust nor count source presence as executed tests.

`AuthoritativeSchemaTarget::StorageV4` remains the default. Explicit AccountsV5 is carried immutably but its production capture gate remains false; five source SQL files per profile are not a schema-5 publication. Legacy user/Device repository calls keep one native result under the pool deadline and bypass generic retry. Late confirmed creation prevents token success while retaining commit facts; unknown or unobserved outcomes never imply no effect. The native engine's bounded attempts and cleanup remain distinct from caller replay and compensation, both of which are forbidden.

`legacy_http_api.rs` is connected to the selected App source authority behind the false AccountsV5 gate, with local ceilings of 512 KiB body/decoded strings, depth 64, 8192 JSON nodes, 2048 members, 128 KiB scalar, 256 vars, 8192 query bytes/64 pairs, 64 KiB Authorization and 2 MiB response. Its seven captured registered extensions apply to message names, not vars keys. First-object trailing input, StdEncoding padding/unused bits/CRLF, null/duplicates and fixed public errors have source/pure tests; actual App/header/authority HTTP execution, full invalid-UTF8 and overlap-create parity, platform resource containment and shared-worker revocation remain unqualified. Source clones share one process-owned service/cache; neither restart persistence nor multi-node coordination is claimed. The old nine-file token claims/source lock is retained with a separate HTTP primary-source subsection. No source check or isolated donor Cargo result confers current-HEAD CI, HTTP or native compatibility credit.

The explicit AccountsV5 target expects 14 tables and retains storage writer epoch 4; its closed production gate prevents this source target from authorizing publication.

The transport drain precheck and App drain admission both use the existing numeric gateway envelope for the four Legacy POST paths and their query variants: HTTP 503, code 14, "Service is draining.", without a retry field. This source repair preserves generic control-route drain behavior and still requires actual HTTP and independent review.

AuthenticateCustom is now an isolated source candidate behind the same false AccountsV5 capture gate. It validates the pinned Custom ID byte predicates (6–128 bytes) and existing username policy, then uses one deadline-bound native lease: a stored-user lookup or one plain autocommit `users` INSERT. Existing users return the stored username and `created=false`; banned users reject before issuance; missing `create=false` skips UUID and writes. A username unique violation returns AlreadyExists, while a concurrent Custom-ID violation returns Internal with no winner lookup, retry, upsert or Device-table effect. The exact SQLSTATE and bounded native failure remain distinct. An observed retired lease cannot begin the Custom write, while cancellation/dispatch races, unknown completion and late confirmed commits remain explicit lease facts and permit no token success or caller replay. The private existing account/session issuer remains shared; its Device name is retained temporarily to keep the original Device body unchanged. Native PostgreSQL/CockroachDB, canonical HTTP, paired upstream and independent acceptance are pending. Default schema 4, writer epoch 4, all ten SQL files and the migration lock remain unchanged.


The AccountsV5 native-auth diagnostic seam is a locally checked source candidate.
The persistence lib-test composes the actual canonical server runtime files in
this repository, with already locked base64/prost-types and explicit
serde_json/raw_value dev dependencies;
it does not copy a server, create a Cargo activation feature or install a new
production binary. An opaque admission has private fields and a production
issuer that retains the closed catalog gate. Only cfg(test) can mint the
crate-private diagnostic admission. The shared pool, lease acquisition,
canonical startup/import verification and native Device/Custom readiness
bodies carry that immutable admission. Public wrappers remain closed even
inside the diagnostic test binary, and mismatched selected targets fail before
TLS material, database access or listener binding. Normal storage business
operations retain their existing production gates; this seam does not qualify
schema5 storage transfer or expand the diagnostic scope to storage parity.

Pinned Rust 1.85.1 local two-crate check, strict Clippy and lib tests passed:
persistence reported 480 passed/2 ignored and server 308 passed. Three
compile-fail admission doctests passed. These finite counts include the
canonical runtime tests composed in the persistence harness; environment-gated
tests without a live fixture grant no database credit. Full workspace/remote
qualification is not implied. The finite
source contract binds the complete local Cargo dependency source closure,
compile-time fixtures, generated-schema inputs and locked dependencies.
Source mutation checks are not a Rust compiler, a cryptographic review or
live execution. Actual auth15 TCP/native pairing, signature verification,
independent review, startup, restore, production and compatibility qualification
remain pending; no public activation or oracle-parity claim is granted.

## Native gRPC source validation

The bounded auth adapter uses the existing pinned Rust 1.85.1, tonic/prost and
Cargo.lock. Canonical bindings are generated under `OUT_DIR/canonical-grpc` in
both server and persistence-test build contexts, preventing the latter from
accidentally using its diagnostic Healthcheck-only definitions. No new dependency
is introduced. Use offline locked scoped checks before any network resolution:

```bash
cargo check --offline --locked -p trnm-server -p trnm-persistence-pg --all-targets
cargo clippy --offline --locked -p trnm-server -p trnm-persistence-pg --all-targets -- -D warnings
cargo test --offline --locked -p trnm-server -p trnm-persistence-pg --all-targets -- --test-threads=1
python3 scripts/check-trnm-server.py
python3 -m unittest discover -s tests/control_plane -p 'test_native_grpc_auth_source.py'
```

The gRPC source contract and transitive native-admission source inventory must
be rebound together after reviewed source edits. This does not qualify native
authentication over gRPC or replace exact PostgreSQL/CockroachDB oracle execution.

The source-validation inventory includes all 50 canonical runtime modules, including
the native gRPC auth/transport/read-storage/mutation/list modules. `grpc-health-source` retains diagnostic
Healthcheck tests/lint and additionally runs canonical native gRPC tests/lint; it
checks the nine-signature source contract without changing the 85-RPC denominator.
Workflow source bytes must be rebound only in the current overlay, not its immutable
base manifest. The active-client regression deliberately records that the existing
absolute 30-second policy also closes healthy clients. Removing that bound requires
a mature transport send-custody deadline, including zero/tiny response windows under
concurrent traffic. Request-future completion or response Body drop is insufficient.

The ReadStorageObjects source adapter uses the same generated native gRPC
service and existing repository method; it does not route through HTTP/JSON.
`cargo test --offline --locked -p trnm-server --lib runtime::grpc::auth::storage`
runs its synthetic-repository, ACL/integrity/timestamp/resource negatives and
actual generated-client loopback tests. The six retained wire vectors are in
`contracts/grpc/nakama-storage-read-protobuf-fixtures.json`. Reproduce their
comparison with `scripts/check-grpc-storage-protobuf-reference.py`, passing
`--upstream-api-proto` for the complete pinned api.proto, `--protoc` for the
reviewed vendored compiler and `--include` for its well-known-type directory.
The runner verifies the complete upstream Git blob before comparing bytes; it
does not download sources or execute Nakama. These diagnostics confer no native
database, immutable process, SDK, runtime-hook or independent acceptance.
Rollback the RPC/proto/adapter/tests and bindings together; there is no data or
credential migration. The existing transport lifetime and AccountsV5 gate stay
unchanged. GetAccount's full-row and online-status authority dependencies remain
open.

The storage mutation adapter lives in `grpc_storage_mutation.rs` with its separate
`grpc_storage_mutation_tests.rs`. Run its focused Rust tests with
`cargo test --offline --locked -p trnm-server --lib runtime::grpc::auth::storage_mutation`.
Keep synthetic repositories and generated-client loopbacks distinct from actual
admitted PostgreSQL/CockroachDB execution. The same protobuf reference runner
now compares all 30 retained vectors: six reads and 24 mutation cases from
`contracts/grpc/nakama-storage-mutation-protobuf-fixtures.json`, against both the
complete pinned upstream schema and the candidate subset. Negative permission
values in those vectors test codec preservation, not API acceptance.

Mutation source controls bind actor/owner, defaults versus explicit-zero wrappers,
raw JSON bytes, the pinned 10,000-container JSON nesting ceiling, exact
code/reason semantic-error selection, opaque OCC, whole-batch commit before
success, precise receipt timestamps and the 100-object/128-character/1 MiB value/512 KiB ingress/2 MiB output
limits. Unknown timestamps or receipt corruption may fail after commit. Never
retry or compensate on projection failure, cancellation or response loss; these
returned receipts are not durable replay records and no storage outbox effect
integration is introduced. Hooks, storage indexes and durable reconciliation
remain open. Rollback reverts the adapter/proto/tests/bindings together without
undoing committed effects. Historical five/six-method observations do not prove
execution of this eight-method candidate; the mutation contract records scoped
Rust results and the known Unix-socket aggregate blocker separately. Exact-head
CI and accepted native qualification remain pending. No gap, milestone, denominator
or production/compatibility claim is promoted.


`grpc_storage_list.rs` and `grpc_storage_list_tests.rs` add the bounded native list
source slice. Its focused command is
`cargo test --offline --locked -p trnm-server --lib runtime::grpc::auth::storage_list`.
The shared protobuf reference runner compares 44 vectors: six reads, 24 mutations
and 14 lists from `contracts/grpc/nakama-storage-list-protobuf-fixtures.json`.
Negative/out-of-range limit vectors prove codec preservation, not API acceptance.
Synthetic tests cover wrapper presence, validation precedence, zero-owner filters,
public/own/foreign ACL, raw projection, literal cursor guards, malformed pages,
redacted failures, the exact encoded budget, shared logout and generated-client
loopback. Source mutation controls bind these fences and all five component states.

The list contract records current validation separately; previous eight-method
results are historical. Actual two-profile SQL continuation/ACL, immutable Nakama
process-global gob IDs, official SDKs, runtime hooks and independent acceptance
remain mandatory. Keep the full denominator and all fail-closed gates unchanged.
Rollback removes the new RPC/messages/adapter/tests and associated bindings
without data or credential migration. No changes to the separate native database
probe are part of this slice.
