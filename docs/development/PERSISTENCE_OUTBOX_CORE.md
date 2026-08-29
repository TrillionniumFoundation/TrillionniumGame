# Rust persistence and transactional outbox core

Status: **source-level implementation candidate**.

This slice defines the deterministic transaction semantics that PostgreSQL and CockroachDB adapters must preserve. It contains no database client, network client, wall clock, scheduler, random source, signer, or external-effect implementation.

## Atomic commit contract

A command commit atomically advances one entity revision, appends a contiguous event range, records the exact idempotent command receipt, updates the state digest, and creates zero or more outbox intents. The prepared result is fenced by both entity revision and authority generation.

An exact duplicate returns the original receipt. Reusing a command ID with another fingerprint is terminal. Reusing an outbox intent ID from another command rejects the whole commit.

## Outbox contract

The outbox uses an explicit lease generation rather than wall-clock logic inside the pure core. Scheduling and lease expiry are adapter responsibilities. A stale worker cannot apply after a retry and re-lease. Replaying the same applied receipt is idempotent; changing the receipt digest is data loss.

## Required adapter evidence

A database adapter must prove:

- one SQL transaction covers entity head, command receipt, events, and outbox rows;
- stale revision/generation updates affect zero rows;
- command and intent uniqueness constraints match the core;
- serializable retries do not duplicate logical effects;
- acknowledgement occurs only after commit;
- crash/restart preserves every acknowledged command;
- outbox lease generation fences stale workers;
- PostgreSQL and CockroachDB remain separate profiles.

This implementation does not claim database durability, Nakama behavior compatibility, SG4 completion, production readiness, or public-online eligibility.
