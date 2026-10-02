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

Batch atomicity, ACL evaluation, server-owned objects, persisted public versions, generated request versions, projection integrity, checked request fingerprints, and OCC conditions.

Non-goals: It does not own JSON HTTP validation, database indexes, query execution, cursor encoding, or wire adapters.

## Architecture and dependencies

Transport adapters validate public requests, this crate decides domain semantics, and persistence adapters enforce the same predicates transactionally.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

`ContentVersion` is the generated lowercase MD5 over exact write-request bytes and remains a `Copy` type. `PublicVersion` is the separate persisted string, checked only for at most 32 Unicode characters. Empty, star, nonhex, uppercase and control strings are legal at this pure boundary; a database adapter separately checks its native text-input rules. `ExpectedVersion` retains the complete incoming condition without normalization or the stored column's length cap. Mutation receipts retain `Some(empty)` for an existing historical version; successful write acknowledgements carry the generated `ContentVersion`.

`StorageObject.value` holds supplied projection bytes and `IntegrityDigest` verifies their SHA-256. A known `CollisionWitness` has private checked fields for request SHA, request byte length, generated request version and projection SHA. It validates the public-version and projection binding and compares incoming request fingerprints for collision hardening. An unknown witness is legal and cannot be fabricated from the rendered value. Neither SHA nor a witness authenticates a source against an actor able to rewrite every field; source-import provenance belongs to the persistence/import boundary.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

`StorageObjectKey::new_nakama` is a narrow projection for rows already governed by
the authoritative schema's 1–128 Unicode-character collection/key constraints.
It permits dot/control text and does not replace the original typed constructor's
128-byte and identifier policy. The client-list adapter uses this factory to
decode valid persisted Unicode identifiers. It grants no complete HTTP write,
index, migration or SDK compatibility claim.

## Correctness and failure model

Blind, create-only, and exact-version writes are distinct. Batch failure is atomic; ACL and version results must be deterministic.

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

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

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
