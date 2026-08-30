# Test and verification policy

Status: binding for plan v3. A test result earns credit only for the exact scope, candidate, environment and assertions recorded in evidence.

## 1. Core rules

1. Empty, absent, skipped, cancelled or older-head check collections are not success.
2. Local results are development feedback, not remote verification.
3. A test must assert behavior; process exit zero with zero assertions earns no compatibility credit.
4. Live prerequisites may be optional for a developer command, but required CI lanes fail when they are missing.
5. PostgreSQL and CockroachDB use separate runs, artifacts and conclusions.
6. A relay run counts only after the target repository validates the exact target commit/tree and artifact digest.
7. Flaky retry does not erase the original result; attempts and final disposition remain recorded.
8. Waivers cannot override identity, ACL, money, durable effects, single-owner, data loss or security-critical failures.

## 2. Test classes

### T0 — static and source contract

- JSON/YAML/schema validity;
- plan/status/gap/evidence consistency;
- generated-source identity and hash checks;
- forbidden dependency/path/boundary checks;
- license, attribution, secret and credential scanning.

T0 may prove source shape only.

### T1 — unit and compile

- Rust format, all-target tests and strict Clippy;
- isolated security-critical workspace tests;
- Go test, race and vet for migration inputs;
- Python unit tests and syntax checks;
- deterministic reference vectors.

T1 may promote a component to `locally-verified` or `remote-verified`, never to behavioral compatibility by itself.

### T2 — property, model and fuzz

- state-machine invariants;
- arbitrary command/retry/restart sequences;
- parser/token/protocol malformed corpus;
- serialization round trips and canonicalization;
- concurrency model and stale-generation rejection.

Every property suite records seeds, corpus digest, engine/tool version, duration and findings.

### T3 — live database

For both profiles:

- fresh empty apply and repeated apply behavior;
- complete catalog introspection;
- negative constraints and malformed rows;
- serializable command/event/outbox transaction;
- exact duplicate receipt and changed fingerprint/digest rejection;
- response loss and restart;
- retry/restart/failover semantics;
- backup, restore and semantic comparison.

Required lane environment:

```text
TRNM_REQUIRE_LIVE_DATABASE=1
TRNM_DATABASE_PROFILE=postgresql|cockroachdb
TRNM_DATABASE_URL=<ephemeral test database>
```

When `TRNM_REQUIRE_LIVE_DATABASE=1`, absence of the URL/profile is a hard test failure. A developer may omit the flag and receive an explicit skip message, but that execution is marked `developer-skip` and cannot produce evidence.

### T4 — wire differential

Compare candidate and immutable Nakama oracle for:

- raw HTTP/gRPC/WebSocket bytes where contractually stable;
- JSON/protobuf mapping and defaults;
- status, headers, gRPC details, retry metadata and close reason;
- CIDs, ordering, cursor/version and reconnect behavior;
- public error text and redaction boundaries.

Inputs, oracle/candidate outputs and normalizer decisions are archived by digest.

### T5 — database and runtime differential

Compare:

- durable rows and reconstructable invariants;
- hook registration/order/context/results;
- runtime module API behavior;
- provider intent/receipt semantics;
- scheduler, match and presence ownership effects.

The oracle and candidate receive isolated clones from the same fixture seed; they never share a writable database.

### T6 — deterministic fault injection

Inject at every durable boundary:

- before transaction;
- after entity CAS;
- after receipt/event/outbox inserts;
- after server commit before acknowledgement;
- process kill/restart;
- network disconnect;
- SQL serialization/deadlock/restart;
- stale lease apply;
- node drain/loss/partition;
- disk/quota/provider failure where supported.

Required invariants:

```text
acknowledged command loss = 0
duplicate visible value effect = 0
stale authority accepted write = 0
partial command/event/outbox commit = 0
```

### T7 — performance, load and endurance

Use approved DEV/COMPAT/PROD-S/PROD-M/STRETCH profiles with exact hardware, topology, workload and cost. Record confidence intervals and same-hardware oracle comparison. Correctness and security assertions run during load.

Required durations are feature/gate specific and may include 24h, 72h and 7d. A shorter smoke never substitutes for endurance.

### T8 — migration, restore and operations

- Nakama snapshot/read/import;
- resumable backfill/CDC receipts;
- semantic comparator;
- write fence and final delta;
- rollback barrier/reverse disposition;
- empty-target restore, PITR and RPO/RTO;
- rolling upgrade/downgrade and incident runbooks.

### T9 — security and privacy

- threat model and abuse cases;
- dependency/provenance/SBOM/advisory checks;
- token/parser/runtime fuzzing;
- key rotation, emergency revoke and secret redaction;
- permission/RBAC/MFA negative matrices;
- penetration testing;
- fixture privacy, retention and deletion.

## 3. Assertion accounting

Every evidence result records:

- assertions total and passed;
- tests collected, executed, skipped and failed;
- property cases and seeds;
- fuzz duration/corpus/crashes;
- live scenarios attempted and completed;
- divergences by severity;
- normalization rules actually used.

A required suite fails when:

- zero tests/assertions are collected;
- a mandatory scenario is skipped;
- result metadata is missing;
- artifact upload is empty;
- the checked-out SHA differs from the candidate;
- a P0/P1 divergence is unexplained.

## 4. CI lanes

The aggregate merge gate runs at least:

```text
plan-and-status
rust-workspace
security-critical-rust
python-contracts
legacy-go-migration-input
schema-authority
```

Live and differential lanes may be separate required checks when infrastructure is available, but the aggregate gate must validate their evidence manifests before promotion. A merge gate cannot report success by silently omitting a required lane based on path filters.

## 5. Path filters

Path filters may reduce unrelated expensive jobs only when a mandatory aggregate job still runs for every PR and verifies that no required lane was omitted. Changes to shared contracts, workspace manifests, lockfiles, generated types, schemas, migrations, status/gates/evidence, workflows or build tooling trigger all affected lanes.

## 6. Flaky tests

A test is flaky when identical source/environment/input can produce different terminal results outside an approved randomness contract. Policy:

- quarantine is time-bounded and owned;
- quarantined tests still run and report but cannot close a gate;
- retry attempts are visible;
- root cause, first/last seen, failure rate and expiry are tracked;
- P0/P1 correctness/security/durability tests cannot be waived as flaky.

## 7. Evidence retention

Evidence artifacts use content digests and retention appropriate to the claim. CI provider retention alone is insufficient for long-lived release evidence; accepted artifacts and manifests are archived in the approved immutable store. The index records location, digest, size, expiry and review.

## 8. Developer commands

A minimum local preflight should provide one command that runs source-valid checks. The exact command is documented in `CONTRIBUTING.md`; it does not imply live, differential or production verification.

## 9. Exit conditions

A capability reaches:

- `source-candidate` after reviewed source and focused T0/T1 tests exist;
- `locally-verified` after applicable local suites pass;
- `remote-verified` after exact-head non-empty CI and artifacts pass;
- `independently-reviewed` after the required reviewer accepts the evidence;
- `accepted` only after denominator, gap, task and gate conditions are all satisfied.
