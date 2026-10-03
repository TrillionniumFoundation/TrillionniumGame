# trnm-storage-core

Status: **module documentation; public-version-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-storage-core`  
Workspace class: `root`
Lifecycle: `product-library`  
Owner role: `storage`

## Status and authority

This document is the current module-level engineering contract for `trnm-storage-core`. Its authority is limited to the module boundary described here: **storage domain and optimistic-concurrency model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `public-version-source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Batch atomicity, stored identifier/ACL domains, operation-specific ACL evaluation, server-owned objects, persisted public versions, generated request versions, projection integrity, checked request fingerprints, and OCC conditions.

Non-goals: It does not own JSON HTTP validation, database indexes, query execution, cursor encoding, or wire adapters.

## Architecture and dependencies

Transport adapters validate public requests, this crate decides domain semantics, and persistence adapters enforce the same predicates transactionally.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

`ContentVersion` is the generated lowercase MD5 over exact write-request bytes and remains a `Copy` type. `PublicVersion` is the separate persisted string, checked only for at most 32 Unicode characters. Empty, star, nonhex, uppercase and control strings are legal at this pure boundary; a database adapter separately checks its native text-input rules. `ExpectedVersion` retains the complete incoming condition without normalization or the stored column's length cap. Mutation receipts retain `Some(empty)` for an existing historical version; successful write acknowledgements carry the generated `ContentVersion`.

`StorageObject.value` holds supplied projection bytes and `IntegrityDigest` verifies their SHA-256. A known `CollisionWitness` has private checked fields for request SHA, request byte length, generated request version and projection SHA. It validates the public-version and projection binding and compares incoming request fingerprints for collision hardening. An unknown witness is legal and cannot be fabricated from the rendered value. Neither SHA nor a witness authenticates a source against an actor able to rewrite every field; source-import provenance belongs to the persistence/import boundary.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

`StorageObjectKey::new_nakama` preserves the pinned source stored collection/key
domain of 0–128 Unicode characters, including empty and dot/control strings.
Its owner bytes are unchanged, including the global all-zero owner. Native SQL
text validity remains the adapter's responsibility. This stored-row factory
does not replace the original `new` constructor's nonempty, 128-byte and
identifier policy, or the HTTP adapter's required collection/key validation.
It grants no complete HTTP write, index, import or SDK compatibility claim.

`ReadPermission` and `WritePermission` are checked private-field wrappers for
the full stored nonnegative SMALLINT domain, 0–32767. `from_stored` rejects
negative cells; `get` and `as_i32` expose the exact number without narrowing or
clamping. Named values are `ReadPermission::{NONE, OWNER, PUBLIC}` and
`WritePermission::{NONE, OWNER}`. The same types carry persisted objects and
typed mutation ACLs, while the HTTP adapter separately restricts new requests
to read 0/1/2 and write 0/1. Stored construction is not request admission.

The predicates intentionally differ: batch-read accepts read exactly 2 or an
owned row with read exactly 1; a collection-wide public list accepts read >=2;
an own-owner list accepts read >=1; a foreign/global-owner list accepts read
exactly 2. Existing client writes require write exactly 1, while client deletes
accept write >0. A client mutation must still bind a nonzero actor to the owner.
`Actor::Server` bypasses ACLs explicitly; an all-zero user is not a server actor.
The core provides these separate numeric predicates without implementing list
queries, native ordering, cursor continuation or a source importer.

## Correctness and failure model

Blind, create-only, and exact-version writes are distinct. Batch failure is atomic; ACL and version results must be deterministic. Create-only first checks actor ownership and then rejects an existing key regardless of its write ACL. Blind/exact writes and deletes retain permission-before-condition evaluation using their different write/delete predicates.

An authorized blind write whose generated token and ACL equal the existing row returns a receipt while preserving its projection and witness. A known request-fingerprint mismatch rejects as DataLoss. Unknown request provenance permits that no-op even when the historical projection differs from the incoming request. Exact writes use the mutation path even for identical request bytes. Native text is never compared to raw request bytes as evidence of an MD5 collision.

The default `StorageState::apply_batch` is explicitly an identity/raw pure model, including its existing binary fixtures; it does not render JSONB. `apply_batch_projected` executes a bounded, side-effect-free supplied projection closure after authorization, OCC, integrity and no-op checks. Projection failure rolls back this model's entire staged batch. The seam supplies no native renderer or database qualification and cannot roll back a caller's external side effects.

Request values retain the candidate 1 MiB bound. Projection bytes have a separate 16 MiB bound; a legal projection beyond that budget returns ResourceExhausted rather than DataLoss. Adapters also own total request, response, transaction and native rendering budgets. These restrictions require explicit profile qualification rather than a universal Nakama size claim.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, and partial-failure behavior must be represented in deterministic tests where applicable.

## Security and privacy

MD5 is used only for request-version compatibility and receives no integrity-authentication credit. SHA-256 detects projection/request corruption but is not a MAC or authenticity proof. Payload, ACL, and identifier bounds are mandatory.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-storage-core --all-targets --locked
cargo clippy --package trnm-storage-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Pure regressions cover empty/128-character Unicode source identifiers, every
nonnegative SMALLINT permission round trip, the six distinct ACL predicates,
owner/global/server visibility, raw ACL and OCC precedence, unknown-witness
blind no-op versus material ACL changes, and staged-batch rollback. These tests
do not prove native SQL, source custody, cursor behavior or HTTP admission.
Focused vectors and live/fault/differential suites are required when this
module's behavior crosses protocol, database, security, realtime, or operational
boundaries.

## Operations

Database adapters own latency, index, storage-growth, and conflict metrics. This core must not perform external I/O.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

The separate bounded client-list SQL/gob projection exists as a source candidate.
Complete JSON validation, database/index behavior, cursor/gateway qualification,
ACL concurrency effects and wire/database oracle differential remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P1-STORAGE-001`
- `GAP-P0-SERVER-001`
- `GAP-P0-DATA-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
