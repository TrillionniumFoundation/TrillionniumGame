# trnm-server

Status: **module documentation; source candidate; no compatibility, durability, SG4, production, public-online, cutover, or retirement credit**

This document is the module-level authority for the first-party Rust server composition package.
It describes the source that exists on this branch and deliberately separates source facts from accepted execution evidence.

## Status and authority

`crates/trnm-server` is the convergence target for the first-party Rust server process boundary.
The package contains the extracted database-backed HTTP, generated gRPC Healthcheck, WebSocket, session, retry, cancellation, and persistence composition source.
The binary target `trnm-server` is a source candidate and is not yet an accepted production authority.
The temporary `trnm-server-foundation` binary remains only as a bounded regression oracle during convergence.
Repository status, gap, evidence, review, and release controls remain authoritative for claim promotion.

## Responsibilities

The module parses and validates process configuration before opening listeners or database connections.
It composes the PostgreSQL/CockroachDB persistence adapter with HTTP, gRPC, WebSocket, session, and command handling.
It preserves acknowledgement-after-durable-commit behavior and exact duplicate-receipt replay.
It applies bounded retry, timeout, cancellation, drain, connection, request, and message policies.
It exports redacted operational metrics without credential, query, token, or private-key labels.
It does not own complete Nakama API, RTAPI, Runtime, Console, provider, IAP, migration, or cluster parity.

## Architecture and dependencies

`src/canonical_main.rs` is the candidate process entry point and `src/runtime/` contains the composed runtime modules.
`src/main.rs` and the library retain the earlier bounded foundation path as a temporary regression oracle.
The package depends on typed contracts, the persistence core and PostgreSQL adapter, realtime wire contracts, session core, and the JWT adapter.
The PostgreSQL adapter remains a library dependency for pool, transaction, schema, session, and cancellation behavior.
The authoritative PostgreSQL and CockroachDB migration sources remain under the repository-level `migrations/` tree.
The final integration change must leave exactly one first-party binary named `trnm-server` and one documented package authority.

## Public contracts

The candidate exposes `check-config`, `migrate`, and `serve` process commands through the canonical binary.
HTTP source includes liveness, readiness, metrics, authenticated drain, authority bootstrap/commit, and session routes.
The gRPC source includes the generated Nakama Healthcheck method on its configured listener.
The WebSocket source supports the bounded JSON and schema-bound protobuf-envelope candidate subprotocols.
Configuration is explicit for bind addresses, public-bind opt-in, database profile, TLS/plaintext policy, pool limits, timeouts, admin credentials, and session verification.
Public error mapping is stable and redacts internal database, credential, and cryptographic detail.

## Correctness and failure model

Every mutating acknowledgement is built only after a durable commit or an exact durable receipt replay.
Command identity, expected revision, authority generation, event sequence, and outbox intent identity are preserved across retries.
Only explicitly retry-safe error classes are retried, within bounded attempt and elapsed-time budgets.
Database deadline and shutdown cancellation retire affected physical connections before pool reuse.
Drain stops new mutations while allowing admitted work and control reads to converge.
Malformed framing, duplicate headers, noncanonical encodings, oversized requests, stalled clients, and configuration ambiguity fail closed.
A source test pass is not equivalent to accepted database durability, protocol parity, or production readiness.

## Security and privacy

Non-loopback listeners require explicit opt-in and implicit plaintext database transport is rejected.
TLS configuration requires verify-full semantics and paired client identity material when client authentication is configured.
Administrative drain and mutation routes require explicit credentials, and diagnostics redact those credentials.
Access-token verification binds issuer, audience, lifetime, subject, token ID, session family, and generation.
Refresh credentials are opaque and hashed before persistence; replay revokes the family without disclosing identity state.
No private key, token secret, database password, raw query, or user identity may be emitted in debug or metric labels.
Independent security and cryptography review remains mandatory for the exact final source and merge object.

## Build and test

The package is an explicit isolated Rust workspace with its own lockfile and Rust 1.85.1 policy.
Required source-candidate commands are:

```bash
cargo fmt --manifest-path crates/trnm-server/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-server/Cargo.toml --all-targets --locked -- -D warnings
```

The cumulative integration gate must also execute the root workspace, all registered isolated workspaces, Python control-plane tests, and Go test/race/vet checks.
Live PostgreSQL and CockroachDB fault lanes, prospective-merge checks, retained artifacts, and independent review are separate required evidence.
Skipped, cancelled, missing, stale, or older-head results receive no credit.

## Operations

The source provides health, readiness, metrics, bounded listener workers, shared drain state, and cancellation accounting.
Operational configuration must use explicit database profile, bounded pool sizing, acquisition deadlines, statement and lock timeouts, and redacted secrets.
Public exposure, capacity limits, rolling upgrade behavior, node loss, partition recovery, and failover require environment-specific qualification.
Backup, restore, PITR, RPO/RTO, endurance, alerting, and runbook acceptance are not established by this module document.
No production deployment, traffic shift, canary, cutover, rollback barrier removal, or legacy retirement is authorized here.

## Compatibility and evidence

The source implements a narrow server foundation and selected HTTP, gRPC, WebSocket, session, command, and persistence paths.
It has not established complete Nakama 1.0 API, RTAPI, Runtime, Console, provider, IAP, storage, social, leaderboard, tournament, matchmaker, party, or multiplayer compatibility.
Compatibility credit requires immutable upstream differential inputs, official SDK black-box coverage, exact database evidence, and independent specialist acceptance.
Evidence must bind the exact source commit, tree, prospective merge object, workflow run, jobs, artifacts, digests, reviewer decision, and expiry.
Repository-controlled CI success may advance a source candidate but cannot self-accept evidence or close externally governed criteria.

## Known gaps and exit criteria

The old persistence-owned `trnm-server` target must be removed atomically when this package becomes the sole binary authority.
Package authority, implementation inventory, status documents, checkers, tests, workflow coverage, and lockfiles must move in the same accepted change.
Complete official protocol and SDK differential coverage remains open.
Real KMS/HSM integration, key rotation operations, certificate rotation, and external-provider evidence remain open.
Multi-node route ownership, partition, node-loss, takeover, HA, capacity, backup/PITR, and endurance evidence remain open.
Migration snapshot, backfill, CDC, semantic comparison, write fencing, shadow, canary, rollback, cutover, and retirement evidence remain open.
Exit requires terminal-success exact-head and prospective-merge packets, retained artifact admission, resolved conversations, conflict-free specialist approval, ordinary protected admission, and accepted post-merge evidence.
Until every linked gap criterion is satisfied, `accepted_evidence=false`, `gap_closed=false`, `production_ready=false`, `public_online=false`, and `nakama_retired=false`.
