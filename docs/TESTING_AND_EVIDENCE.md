# Testing and evidence

Status: **authoritative current documentation**  
Revision: 2026-09-05

## 1. Proof rule

A result earns credit only for the exact candidate, scope, environment and assertions recorded by a valid evidence item. The following never count as success:

```text
missing or empty collection
zero-job workflow
skipped, cancelled or neutral required job
startup failure
older-head result
local-only execution
log or screenshot without retained digest
unreviewed evidence where review is required
```

PostgreSQL and CockroachDB use separate runs, artifacts and conclusions. A passing profile does not cover the other.

## 2. Test classes

### T0 — source and control contracts

JSON/YAML/schema validity, documentation authority, plan/status/gap/evidence consistency, generated-source identity, forbidden dependency/path checks, workflow policy, repository identity, licenses and secret scanning. T0 proves source shape only.

### T1 — build and unit

Rust format, all-target tests and strict Clippy; isolated security-critical workspaces; Go formatting/test/race/vet; Python compilation and test discovery; deterministic vectors. T1 alone cannot establish behavior compatibility.

### T2 — property, model and fuzz

State-machine invariants, arbitrary retry/restart sequences, malformed token/parser/protocol corpora, serialization canonicality, concurrency and stale-generation rejection. Record seed, corpus digest, tool version, duration and findings.

### T3 — live database

For each profile: fresh/repeated migration, catalog introspection, negative constraints, serializable command/event/receipt/outbox, duplicate replay, changed fingerprint, stale revision/generation, response loss, restart, retry behavior, backup and semantic restore.

Required lanes set:

```text
TRNM_REQUIRE_LIVE_DATABASE=1
TRNM_DATABASE_PROFILE=postgresql|cockroachdb
TRNM_DATABASE_URL=<ephemeral database>
```

Absent required configuration is a hard failure. Developer-only omission may produce an explicit no-credit skip.

### T4 — wire differential

Compare raw HTTP/gRPC/WebSocket behavior, JSON/protobuf mapping, defaults, status, headers, details, retry metadata, CID, ordering, cursor/version, reconnect and close behavior against the immutable oracle.

### T5 — database and runtime differential

Compare durable invariants, hook order/context/result, Runtime module APIs, provider intent/receipt, scheduler, match and presence effects using isolated clones from the same seed.

### T6 — deterministic fault injection

Inject before transaction, after each durable write boundary, after commit before acknowledgement, during response loss, process restart, SQL serialization/deadlock/restart, stale lease apply, node drain/loss/partition, storage exhaustion and provider ambiguity where applicable.

Mandatory invariants:

```text
acknowledged command loss = 0
duplicate visible value effect = 0
stale authority accepted write = 0
partial command/event/outbox commit = 0
```

### T7 — load and endurance

Run approved hardware/topology/workload profiles, preserve same-hardware oracle comparison and continue correctness/security assertions during load. Required durations may be 24h, 72h or 7d; a short smoke is not endurance.

### T8 — migration and operations

Snapshot/import, resumable backfill/CDC, semantic comparator, write fence, rollback barrier, reverse disposition, empty-target restore, PITR, rolling upgrade/downgrade and incident rehearsal.

### T9 — security and privacy

Threat model, abuse cases, dependency/provenance/SBOM/advisory checks, fuzzing, key rotation/revoke, secret redaction, RBAC/MFA negative tests, penetration testing and fixture retention/deletion.

## 3. Assertion accounting

Evidence records tests collected/executed/skipped/failed, assertions total/passed, property cases/seeds, fuzz corpus/duration/crashes, live scenarios attempted/completed, divergences and normalizers used.

A required suite fails when it collects zero assertions, skips a mandatory scenario, lacks result metadata, produces an empty artifact, checks out the wrong SHA or leaves an unexplained P0/P1 divergence.

## 4. Aggregate CI

`trillionnium-game-merge-gate` is the stable required aggregate. It must run for every pull request and reject omitted dependencies. Its source lanes include:

- documentation, plan, status, gap, evidence and schema control;
- workflow/action syntax and policy;
- root Rust workspace;
- every registered isolated Rust workspace;
- complete Python discovery;
- Go test/race/vet migration input;
- server and schema source contracts;
- current-head external workflow collection.

Expensive live or differential workflows may run separately, but the aggregate must validate that all required current-head families are present, non-empty, terminal and successful before promotion.

## 5. Exact-head workflow identity

Every workflow fetches and checks out the exact candidate SHA rather than trusting a mutable branch checkout. A verifier binds repository, workflow ID/path, run ID, run attempt, head SHA, tree, expected job set, runner assignment, non-empty steps and terminal conclusions.

When a head changes, all previous results become diagnostic for the new candidate. Rerunning an old commit cannot qualify a newer tree.

## 6. Evidence manifest

The authoritative index is `docs/evidence/index.json`; the schema is `docs/evidence/schemas/trillionnium-evidence-v1.schema.json`.

A promotable item records:

- evidence ID/type and mapped claim/gate/task/parity IDs;
- exact upstream and candidate repository/commit/tree/artifact identities;
- OS, architecture, database, toolchain, timezone, locale and configuration digest;
- fixtures and commands;
- start/completion timestamps;
- assertions, metrics, normalizers and divergences;
- retained artifacts with media type, size and SHA-256;
- limitations and expiry;
- independent review decision and identity.

An index entry appears exactly once. Relay evidence receives no credit until the target repository validates the exact target identity and artifact digest.

## 7. Retained artifacts

CI-provider retention alone is not sufficient for long-lived release proof. Accepted evidence is copied to the approved immutable store and indexed by digest. Diagnostic artifacts are clearly distinguished from passed evidence.

Where repository policy disallows external upload actions, bounded binary evidence may be sealed into the exact downloadable GitHub job log. Its verifier must reconstruct the full archive from the completed provider log, validate timestamp framing, reject ambiguous/duplicate/truncated envelopes and recheck name, size, SHA-256, candidate, tree, run, attempt, job, profile and migration blob.

## 8. Database and outbox evidence

Current live workflows exercise both database profiles with immutable OCI digests. The outbox final-attempt profile covers crash-before-publish and crash-after-publish, reaper-only terminal counts and in-place terminal state. This is scoped evidence; it does not establish exactly-once semantics for every future effect adapter.

Outbox proof must distinguish:

- acknowledged durable state;
- intent lease/reclaim/final transition;
- external publish attempt;
- provider receipt or ambiguous outcome;
- reconciliation result.

Possible lost effects in a deliberately terminal pre-publish scenario must be stated rather than hidden behind an exactly-once claim.

## 9. Reviews

Automation, implementation authors, generated summaries and PR prose cannot provide independent acceptance. Required reviewers bind their decision to the exact commit/tree and evidence scope. Review expires on head change, evidence expiry, upstream change or invalidated environment/artifact identity.

P0/P1 gap closure requires accepted independent review as defined by the gap register. `review-requested` and `COMMENTED` are not approvals.

## 10. Flaky and quarantined tests

Retries remain visible. Quarantine is owned, time-bounded and still executed/reported; quarantined results cannot close a gate. Correctness, security, identity, authorization, money, durability and single-owner tests cannot be waived as flaky.

## 11. Developer commands

The minimum local suite is documented in [`DEVELOPMENT.md`](DEVELOPMENT.md). It is development feedback only. Promotion requires the applicable exact-head CI, live/differential/fault layers, artifacts and review.

## 12. Evidence status boundary

The current evidence index has no accepted repository-wide compatibility entry. Historical/directed evidence records remain available for audit but cannot become current authority merely because their files are present. Product gates remain evidence-derived and fail closed.

## 13. Shared retained-evidence admission

`scripts/evidence_admission.py` is the shared structural eligibility contract for
`check-evidence-index.py`, `check-gap-register.py`, `derive-gap-status.py`,
`derive-gates.py` and `check-status-transitions.py`. An `accepted` status alone,
`review.decision=accepted`, or the `manifest` evidence type cannot substitute for
explicit credit, schema validity, exact target binding and independent review.
All five consumers reject the same incomplete evidence rather than applying
separate progressively weaker predicates. Existing historical diagnostics remain
readable and non-creditable; they are not silently converted to accepted records.

Admission requires the canonical repository and lowercase commit/tree IDs; the
exact `independent=true` and `self_review=false` pair; nonblank reviewer identity
and role; matching reviewed commit/tree; timezone-qualified timestamps; review
after successful execution; and unexpired evidence. Conflicting alias fields,
duplicate JSON keys, non-finite JSON numbers, accepted-without-credit records and
missing index-policy keys fail closed. Closed-gap type coverage and a single
candidate cohort remain mandatory; mixed targets cannot collectively pass a gate.

A credited entry must retain a local manifest and its referenced artifact bytes in
the checked evidence root. The validator evaluates the keyword subset used by the
existing `trillionnium.evidence.v1` schema, rejects unknown validation keywords and
remote references, and compares index/manifest IDs, target, mappings, review,
expiry and artifact sets. It hashes the actual retained bytes, not just the text
of a supplied hash. Missing, empty, truncated or changed files, duplicate artifact
names/paths, self-referencing evidence, path traversal and symlink components are
rejected. Assertions must be nonempty and completely passing; unresolved P0/P1
divergences remain blockers. Artifacts held externally must be staged from the
approved immutable store for validation; a URL or digest alone earns no credit.

Limits are 8 MiB per JSON document, 256 artifacts, 64 MiB per artifact and 256 MiB
of retained artifacts per item. Schema traversal is limited to 64 levels and
100,000 visits. Larger evidence requires an explicitly reviewed retention profile,
not silent truncation or disabling the check. Python control scripts use only the
standard library; this bounded validator is not a general JSON Schema engine.

```bash
python3 -m unittest tests.control_plane.test_evidence_admission -v
python3 scripts/check-evidence-index.py
python3 scripts/check-gap-register.py
python3 scripts/derive-gap-status.py
python3 scripts/derive-gates.py
python3 scripts/check-status-transitions.py
```

The regression suite uses synthetic accepted fixtures only to exercise positive
and negative admission. It does not invent real reviewer decisions. Structural
checks and retained-byte validation do not establish GitHub review provenance,
latest live candidate identity, administrator policy, native execution, oracle
compatibility or production acceptance. Those independent prerequisites remain
required. The source fix changes no runtime Rust, database schema, protocol,
workflow definition or required-workflow denominator and closes no gap by itself.


## 14. Database negative attribution and production retry proof

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
