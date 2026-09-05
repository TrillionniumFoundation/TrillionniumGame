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
readable and non-creditable; they are not silently converted to accepted evidence.

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

## 15. Descriptor-bound retained-file reads

Retained JSON, manifests, schemas and artifacts are opened by walking from the
filesystem root through directory descriptors. Every path component uses
`O_NOFOLLOW`; directories use `O_DIRECTORY`, and descriptors use `O_CLOEXEC`.
The leaf is opened with `O_NONBLOCK` before `fstat` requires a regular file. A FIFO
substituted immediately before open therefore cannot block waiting for a writer.
There is no fallback to a path-based open when these POSIX capabilities are absent.

Size, regular-file type, byte limits and the final metadata snapshot are checked
against the same opened descriptor that supplies the hashed bytes. A leaf or
unopened ancestor changed to a symlink is rejected. Replacing an already opened
parent path cannot redirect subsequent relative opens: they stay anchored to the
original directory inode. This does not promise that all directory names remain
unchanged; it prevents following the substituted path to different contents.

The descriptor snapshot includes device, inode, type/mode, link count, size,
mtime and ctime. Growth, truncation, unlink and observable in-place mutation during
reading fail validation. These checks do not defeat privileged mount changes or a
compromised kernel. They are not a cryptographic filesystem snapshot or a substitute
for an immutable retained store and authenticated review provenance.

The existing byte budgets and nullable expiry semantics are unchanged. Artifact
reads stream in chunks no larger than 1 MiB, with a bounded extra-byte probe;
absolute path walks allow at most 256 components including the root. These are
byte and iteration limits, not a hard wall-clock deadline for stalled regular-file
I/O on a failing disk or network filesystem. Such storage requires an external
process timeout. Operating-system diagnostic text and retained contents are not
included in the helper's public I/O error message.

```bash
python3 -m unittest discover -s tests/control_plane -p 'test_evidence_retained_io.py' -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The I/O suite uses deterministic syscall-boundary mutations, not sleep-based race
assumptions. It covers leaf/parent swaps, FIFO handling, manifest/schema consumers,
metadata and length changes, descriptor cleanup, read budgets and absent platform
support. A bounded child process separately guards the FIFO regression. Its
synthetic fixtures and local results grant no independent acceptance. The
source-candidate scope is recorded in
[`status/EVIDENCE_RETAINED_IO_STATUS.json`](status/EVIDENCE_RETAINED_IO_STATUS.json).
Neither gap statuses nor product gates are promoted by this repair. Native
exact-head/prospective-merge qualification and independent acceptance remain required.

The foundation dependency checker also binds the existing diagnostic OpenSSL dependency
exactly to `=0.10.81`, matching Cargo.toml and the already pinned Cargo.lock.
It does not allow version ranges, alternate sources, extra dependencies or pure-core
OpenSSL imports. `test_rust_foundation_dependency_alignment.py` checks positive
repository validation and rejected mutations, including other pins and runtime
feature changes. This alignment is not an independent cryptographic approval.

## 16. Shared persistence dependency policy

The canonical server checker reads `EXPECTED_DEPENDENCIES` from the sibling
`scripts/check-rust-foundation.py`; it no longer carries a second persistence
dependency table. The sibling is resolved from the checker file rather than the
working directory or import search path. Missing or malformed policy fails
without a fallback, and callers receive a deep copy. The existing exact runtime
pins, internal paths and feature lists remain enforced, including the diagnostic
OpenSSL pin. The server's protobuf build-dependency table remains unchanged.

`tests/control_plane/test_trnm_server_dependency_contract.py` exercises the real
`check-trnm-server.py` executable in complete test discovery, in addition to the
other foundation/slice checkers. It also rejects missing/extra dependencies,
ranges, alternate sources, feature changes, unavailable policy, omitted required
source and removed aggregate invocation. These regressions ensure an unrelated
slice checker cannot stand in for the canonical server source contract.

```bash
python3 scripts/check-trnm-server.py
python3 scripts/check-rust-foundation.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

This source-contract repair changes no Cargo manifest, lockfile, Rust runtime,
DDL, workflow definition, deadline, required-job set or acceptance condition.
It does not establish database behavior, independent cryptographic review or
production readiness. Exact-candidate execution and independent acceptance
remain required for every applicable gap.

## 17. Actions artifact request encoding

The native uploader emits `CreateArtifact.mime_type` at the top level, as a
ProtoJSON scalar string. The obsolete `metadata.wrapper.mime_type` shape is not
part of the inspected version-seven request. The protocol reference is GitHub
`actions/toolkit` commit `6fe3c0f3e61b5f34b85f28067d82e7e3ffcb312f`, generated
`packages/artifact/src/generated/results/api/v1/artifact.ts`, blob
`dbdd7bbb7ae3a1932bed8e877d82a235873ed08b`. Its StringValue fields use ProtoJSON
scalar encoding. Finalization retains its scalar hash and string int64 size.
No existing repository/job identity, digest, upload bound or retry rule changes.

The original upload test's expectation is corrected rather than deleted.
`tests/control_plane/test_actions_artifact_protocol.py` sends the actual uploader's
serialized requests through a strict offline service fixture. It exercises ZIP
and gzip, retry identity, nonretryable create refusal, rejected blob upload,
failed finalization, nonempty input, MIME validation and absence of Authorization
on the direct signed-blob PUT. It rejects the old nested request shape. This is
not a complete redirect-security audit or a claim about a remote service run.

```bash
python3 -m unittest discover -s tests/control_plane -p 'test_actions_artifact_*.py' -v
```

A successful local fixture is not an Actions upload receipt. Real retention must
record a nonempty finalized artifact ID, exact producer run/attempt/job, then
download the bytes and verify their size, digest and member identities. Product
qualification and diagnostic export have different source identities; neither
can silently stand in for the other. No new product, gap or independent-review
credit follows from this field correction. The source boundary is recorded in
[`status/ACTIONS_ARTIFACT_UPLOAD_STATUS.json`](status/ACTIONS_ARTIFACT_UPLOAD_STATUS.json).

## 18. Upload file custody and redirect boundaries

The native uploader applies descriptor-relative no-follow I/O on its supported
POSIX runner. It opens every ancestor directory with `O_DIRECTORY` and
`O_NOFOLLOW`, captures the final entry without following links, and requires
the opened descriptor's device, inode, mode, link count, size, mtime and ctime to
match that inspected entry before reading any bytes. The final descriptor and
anchored entry are checked again. Leaf symlinks, equal-sized inode substitutions,
special files and observable changes fail closed. `O_NONBLOCK` prevents a replaced
FIFO from waiting for a writer before the regular-file check. Missing platform
capabilities have no path-reopening fallback.

Each read is at most 1 MiB and the total bytes requested are limited to the
inspected length plus a one-byte growth probe, always at most the existing
64 MiB artifact bound plus one. Empty files, growth, truncation and detectable
metadata changes are rejected. Descriptors are released on success and failure.
This is not an immutable filesystem snapshot, protection against a privileged
mount/kernel adversary, or a hard wall-clock deadline for stalled regular-file I/O.
An already-opened parent remains the anchor if its directory name is replaced;
that replacement cannot redirect reads into different contents.

The production transport builds a private urllib opener that refuses every
automatic 301/302/303/307/308 redirect for CreateArtifact, FinalizeArtifact and
signed-blob PUT, including same-origin and HTTPS-to-HTTP redirects. Refusal is
terminal, closes the redirect response without consuming its body, and emits no
Location, token or signed query. It does not inherit a globally installed urlopen
opener. Twirp Authorization is also added as an unredirected header, so Python's
standard redirect-request construction cannot copy it to another request. The
direct signed PUT still carries no runtime Authorization. Trusted injected test
openers remain a local API/testing boundary, not a mechanism for accepting
server-directed redirects. No redirect allowlist or insecure fallback is added.

`test_actions_artifact_security.py` contains deterministic inode/symlink/FIFO,
growth/truncation, descriptor-cleanup and parent-anchor regressions. Its offline
transport replaces only network I/O; the redirect tests execute the real urllib
OpenerDirector and HTTP error dispatch for all five redirect statuses and
cross-origin, same-origin, relative and downgrade targets. Existing protocol
tests still verify that authorization is present on the original Twirp requests
and absent from the signed PUT. The same-origin redirect target is selected per
phase: the signed PUT uses its Blob origin, while Create/Finalize use Results.
The tests compare each target label with the actual outgoing request origin;
the previous shared Results target did not exercise PUT same-origin redirects.
This fixture correction reruns the existing cases and adds no test-count credit.

```bash
python3 -m unittest discover -s tests/control_plane -p 'test_actions_artifact_*.py' -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

These tests use only synthetic data and credentials and never contact a remote
service. The two source findings from review 5120205543 are addressed by this
implementation, not independently accepted by its author. Earlier native upload
and retention results bind the prior uploader and cannot qualify this revision.
New exact-head/prospective execution, real upload/download validation and fresh
conflict-free review remain required. Reverting would restore the inspected
file/credential boundary defects; no DDL, Rust business behavior, workflow
definition, required denominator or production claim changes with this repair.

## 19. Bounded service replies and canonical artifact identity

Artifact upload requests and retained file bytes are separate from the service's
small acknowledgements. `upload-actions-artifact.py` caps each successful reply,
including the signed PUT acknowledgement, at 1 MiB plus a one-byte oversize probe.
The 64 MiB artifact limit is unchanged. Responses exceeding the reply limit are
rejected before JSON parsing or advancement to the next upload phase. Error
status bodies are not consumed, and raised HTTP responses are closed before
retry or rejection; retry statuses and the five-attempt ceiling do not change.

Create/Finalize responses must be UTF-8 JSON objects with at most 64 container
levels and number tokens of at most 128 characters. Duplicate keys (including
escaped spellings and nested duplicates), NaN/Infinity, floating-point overflow
and multiple spellings of the same consumed field fail closed. Either snake_case
or camelCase is accepted individually, not both together. Bounded, finite unknown
extension fields remain accepted. This is a narrow service-response parser, not a
general JSON Schema or full ProtoJSON conformance implementation.

A successful FinalizeArtifact reply must contain a positive signed-int64 artifact
ID. Canonical ASCII decimal strings and positive integer JSON values are accepted;
booleans, floats, null, zero, negative/overflow values, whitespace, leading zeros,
exponent spellings and embedded control characters are rejected. This positive-ID
profile is deliberately narrower than generic ProtoJSON numeric parsing. The
inspected GitHub generated response declares an int64 field; successful retained
native replies previously observed in this repository use canonical decimal IDs.
No response value can add a line to GITHUB_OUTPUT. A rejected final reply creates
neither a success receipt nor new output-file entries; it also does not prove the
remote service rolled back or deleted any previously uploaded bytes.

`test_actions_artifact_response_contract.py` invokes the real uploader and CLI
using synthetic replies only. It checks valid and invalid IDs, contradictory
status/identity fields, nested data bounds, exact byte/depth boundaries, escaping,
closed HTTP responses and retry preservation. In particular, it verifies the
output file remains untouched after a reply containing an injected output line.
No test uses real credentials or contacts a service. Existing no-follow file
custody, no-redirect transport, original-request authentication and credential-free
signed PUT tests continue to run without being weakened.

```bash
python3 -m unittest discover -s tests/control_plane -p 'test_actions_artifact_*.py' -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The component status maps this work to TG-V3-002 and the existing evidence/CI/test
gaps. This source candidate supplies no independent acceptance, current native
upload proof or formal gap closure. It changes no Rust runtime, DDL, workflow
definition, credentials or required-job denominator. Old native uploads do not
qualify this revised parser. Publishing requires exact-source/prospective CI,
a fresh real upload/download check, retained bytes and independent review.

## 20. Task and gate scope binding

An accepted execution task cannot define its own smaller proof obligation. The
digest-verified execution backlog supplies task identity, dependencies and gate
membership. The digest-verified product-gate semantic scope supplies blocking
gaps and evidence classes. The current milestone similarly binds item identity,
dependencies, gaps, deliverables, acceptance clauses and required evidence while
leaving status, target and evidence references mutable. A change to those
immutable semantics requires a reviewed source change and a new digest; editing a
status overlay cannot erase them.

For an accepted backlog task, all derived blockers must be closed and every
required product gate must be passed for one exact repository/commit/tree. The
retained task evidence must map to the task and each gate and cover every required
evidence class. Roadmap tasks obey their exact declared dependency/gap/evidence
scope. Evidence manifests may use canonical backlog task IDs (`TG-W<n>-<nnn>`)
or current Plan-v3 roadmap IDs (`TG-V3-<nnn>`); malformed IDs and unapproved
version namespaces fail schema admission.

The regression suite includes positive synthetic retained evidence and hostile
status-only, scope-shrink, wrong-target, missing-gate and type-coverage cases. It
does not create real reviews or accepted product evidence. Current-head and
prospective-merge execution, retained artifact custody, independent review,
administrator read-back and every semantic close criterion remain separate.

```bash
python3 -m unittest tests.control_plane.test_evidence_admission -v
python3 -m unittest tests.control_plane.test_execution_acceptance -v
python3 scripts/check-status-transitions.py
python3 scripts/check-plan.py
```

## 21. Gap-bound evidence mappings

Accepted retained evidence carries a nonempty canonical `gap_ids` mapping in both
the index row and the retained manifest. The shared admission contract compares
those arrays byte-for-meaning with the other claim/gate/task/parity mappings. A
closed gap may cite only evidence whose `gap_ids` explicitly contains that exact
gap ID; sharing the same evidence type, candidate commit/tree, reviewer or artifact
is not sufficient. This prevents unrelated accepted evidence from being replayed
to close another gap with a compatible type requirement.

The schema accepts only `GAP-P0-*`, `GAP-P1-*` and `GAP-P2-*` canonical identifiers.
Missing, empty, duplicated, malformed or index/manifest-divergent mappings fail
before credit. Existing diagnostic and legacy rows remain readable because they
do not receive admission credit; any future accepted record must use the revised
retained manifest contract.

`test_evidence_for_one_gap_cannot_close_another_gap` reproduces the previous
cross-gap credit path and verifies that correcting the explicit mapping is the only
way the same retained fixture can qualify. The real gap-register consumer has a
separate regression, so a downstream wrapper cannot silently weaken the shared
rule. Synthetic fixtures do not constitute real gap acceptance.


## 22. Immutable gap closure scope

A gap cannot define a smaller proof obligation in the same status edit that
claims progress. The shared admission module canonically binds every registered
gap's identity, severity, category, owner, blocked claims, affected paths, close
criteria, evidence classes, issue references and external-dependency contract.
The gap set, status vocabulary and closure policy are part of the same digest.
Set-like list ordering does not affect the digest, but removing or changing any
semantic value requires an explicit reviewed repin.

Mutable fields remain status, `evidence_ids` and the resolved runtime value of
`external_dependency`. Before closure the runtime value must equal the immutable
external-dependency contract. A closed row may clear it only after the normal
retained-evidence checks pass; the contract itself remains in the immutable
projection. This prevents a P0/P1 severity downgrade, criterion deletion, evidence
type reduction, gap removal or hidden external dependency from becoming a
status-only acceptance path.

`check-gap-register.py`, `derive-gap-status.py`, `derive-gates.py` and
`check-status-transitions.py` all invoke the same validator. Regression tests
mutate every protected field, the gap set, status vocabulary, closure policy,
declared digest and external-dependency state. Synthetic scope tests grant no gap
closure, execution credit, review or production authority.

```bash
python3 -m unittest tests.control_plane.test_evidence_admission.GapScopeTests -v
python3 -m unittest tests.control_plane.test_evidence_admission.ConsumerWiringTests.test_every_gap_consumer_invokes_shared_scope_validation -v
python3 scripts/check-gap-register.py
python3 scripts/derive-gap-status.py
python3 scripts/derive-gates.py
python3 scripts/check-status-transitions.py
```
