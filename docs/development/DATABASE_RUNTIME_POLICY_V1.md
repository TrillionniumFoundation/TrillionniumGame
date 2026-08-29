# Database runtime policy v1

Status: **pure source candidate; no connection pool or TLS adapter is implemented yet**.

## Purpose

`crates/trnm-persistence-runtime-policy` converts the database runtime requirements into pure deterministic policy before introducing a specific async pool/TLS dependency. It prevents an adapter from choosing unbounded retries, disabling TLS in production, retrying an external effect, or allowing inconsistent timeout ordering.

## Policy boundary

The policy separates:

- database profile: PostgreSQL or CockroachDB;
- deployment class: developer, compatibility or production;
- TLS mode and opaque certificate/key handles;
- pool size/lifetime/acquire limits;
- lock, statement and transaction deadlines;
- finite retry attempts, backoff and total deadline;
- operation class and retry eligibility;
- cancellation and resync behavior.

## Retry eligibility

Automatic retry is allowed only for:

```text
read-only operation
idempotent durable command
outbox receipt apply
```

A non-idempotent external effect is never retried by this policy. Such effects use durable intent identity, receipt reconciliation and an outbox worker outside the command transaction.

`RetryClass::ResyncRequired` never performs an automatic retry. Revision or ownership mismatch must return control to the protocol/domain layer.

## Required adapter integration

A production database runtime must:

1. choose and lock a reviewed async PostgreSQL-compatible driver and pool;
2. use TLS verify-full for compatibility/production profiles;
3. obtain CA/client identity through opaque secret providers;
4. apply acquire, lock, statement and transaction deadlines;
5. propagate cancellation;
6. classify SQLSTATEs through the stable domain mapping;
7. use the pure policy before every retry;
8. recreate or correctly restart transactions after serialization failure;
9. record attempt, elapsed/deadline, profile, SQLSTATE class and final outcome metrics;
10. run PostgreSQL and CockroachDB as separate evidence profiles.

CockroachDB restart behavior cannot be inferred from PostgreSQL success. Each profile needs its own transaction, failover and query-plan evidence.

## Source gate

```bash
cargo fmt --manifest-path crates/trnm-persistence-runtime-policy/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-persistence-runtime-policy/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-persistence-runtime-policy/Cargo.toml --all-targets --locked -- -D warnings
```

## Claim boundary

This crate defines policy only. Pool, TLS, retry execution, durability, HA, C2/C4, SG4/SG8 and production readiness remain false until an adapter and exact live evidence exist.
