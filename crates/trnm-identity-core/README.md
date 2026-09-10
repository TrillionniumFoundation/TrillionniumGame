# trnm-identity-core

Status: **module documentation; account-and-provider-identity-source-candidate; no automatic compatibility or production credit**
Path: `crates/trnm-identity-core`
Workspace class: `root`
Lifecycle: `product-library`
Owner role: `identity`

## Status and authority

This crate is the transport- and persistence-independent account and provider-identity state machine. It is a bounded source candidate for the first part of Plan v3.2 issue #137. It is not a complete Nakama authentication service, provider verifier, public API, database implementation, or production identity authority.

## Responsibilities

It owns typed account, username, display-name, provider-identity, status, revision and command-receipt transitions. The implemented provider classes are custom, device, email, Facebook, Facebook Instant Game, Apple, Google, Game Center and Steam.

It enforces unique live usernames and provider identities, exact command replay, changed-fingerprint rejection, revision fencing, bounded account/identity/receipt state, link/unlink rules, active/disabled/banned status and terminal deletion tombstones.

Non-goals include JWT or provider-token verification, password hashing, database access, HTTP/gRPC mapping, session issuance, provider callback I/O, account export, wallet/metadata and migration.

## Architecture and dependencies

The crate has no external dependencies and performs no I/O. Public adapters validate provider assertions and construct typed commands; a durable repository atomically stores the resulting account state, reverse indexes and command receipt.

The in-memory registry clones its bounded candidate state and commits only after all invariants pass. This is an explicit correctness model, not the target storage implementation or a high-scale performance claim.

## Public contracts

Account, command and fingerprint identities reject all-zero values. Usernames, display names and provider identities have byte and control-character bounds. Provider identity and username uniqueness are global inside one registry authority.

Every mutating operation names an exact expected account revision. Receipt replay is operation-scoped: a command receipt created by another operation is a conflict even when its fingerprint matches. Authentication replay is returned as current authority only while the provider still resolves to the receipt account, that account remains Active, and its revision is exactly the revision recorded by the authentication receipt. Any profile, provider or status mutation makes the old authentication receipt stale; deletion, unlink and rebind fail closed. A changed fingerprint is always a conflict and all denied replays leave state unchanged. Trusted adapters must derive the fingerprint from the canonical complete operation and provider input rather than accepting an arbitrary caller-selected value.

Deletion is terminal inside the registry: the record remains, and former usernames and provider identities are retired rather than silently rebound.

## Correctness and failure model

Failed validation, capacity, collision, stale revision, inactive status, last-provider removal and counter overflow leave the complete registry unchanged. Revision increments are checked before mutation. Authentication receipt replay evaluates fingerprint and operation type first, then current provider ownership, account activity and exact revision; a historical receipt never bypasses a later disable, ban, deletion, unlink, rebind, reactivation or other account mutation.

Account/provider/username reverse indexes are verified after each accepted transition. Every non-deleted account has at least one provider identity. A deleted account has no active reverse-index entries and all former identities appear in retirement sets.

A mutable Rust value is not a distributed ownership proof. Production adapters must provide a single writer, serializable atomicity, durable receipt replay, crash recovery and migration fencing.

## Security and privacy

Provider external identifiers are untrusted identifiers, not provider credentials or proof of authentication. Provider token validation, audience/application binding, nonce/replay checks, deadlines and secret handling must occur in reviewed adapters before this core is called.

Raw provider tokens, passwords, credentials and personal payloads are not represented by the API. External identifiers and usernames must not become unbounded metric labels or be logged without an approved redaction class.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-identity-core --all-targets --locked
cargo clippy --package trnm-identity-core --all-targets --locked -- -D warnings
```

Tests cover bounds, operation-scoped exact replay, changed fingerprints, provider and username collisions, stale revisions, last-provider protection, profile changes, account status, terminal deletion, receipt exhaustion and revision overflow. Authentication-specific regressions cover unchanged active replay plus disable, ban, reactivation, deletion, unlink, rebind, revision drift and cross-operation command reuse, with complete no-mutation assertions for every rejection.

These tests are source-level evidence only. Exact-head CI, public-wire differentials, live database faults and independent review remain separate.

## Operations

This crate starts no process or task. Adapters must expose bounded low-cardinality counts for accepted/rejected operations, revision conflicts, collisions, status changes, retirement and receipt replay.

The production service must define readiness dependencies, provider health, database timeout/cancellation, audit retention, recovery and abuse/rate limits.

## Compatibility and evidence

No Nakama compatibility credit is claimed. Required closure includes denominator-leaf mapping, exact HTTP/gRPC and official-SDK differentials, provider sandbox/hostile fixtures, PostgreSQL transaction and response-loss evidence, session integration and independent identity/security review.

Provider-specific normalization, case handling, account merge/collision behavior, username generation, create/no-create behavior and public errors remain profile and oracle decisions.

## Known gaps and exit criteria

Blocking work remains under `GAP-P0-SERVER-001`, `GAP-P0-DATA-001`, `GAP-P0-CRYPTO-001`, `GAP-P0-SCOPE-001`, `GAP-P1-REVIEW-001` and issue #137.

Exit requires typed service and durable repository adapters, every relevant denominator leaf mapped to implementation and tests, official consumer/oracle differential, exact-object retained evidence and conflict-free independent acceptance. This crate alone does not close issue #137 or any product gate.
