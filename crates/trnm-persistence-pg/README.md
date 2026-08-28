# trnm-persistence-pg

Synchronous PG-wire persistence adapter candidate for the W1 foundation schema.

## Implemented slice

- native Rust PostgreSQL protocol client (`postgres = 0.19.14`), not a `psql` or subprocess wrapper;
- distinct PostgreSQL and CockroachDB schema metadata profiles;
- serializable command transaction;
- exact duplicate receipt replay;
- conflicting command fingerprint rejection;
- entity revision and authority-generation fencing;
- entity-head compare-and-swap;
- atomic command receipt, event and transactional-outbox insertion;
- stable SQLSTATE-to-domain-error classification;
- reconnect-and-replay live contract for post-commit response-loss semantics.

The live test is skipped when `TRNM_DATABASE_URL` is absent and is executed by the relay database workflow against fresh PostgreSQL and CockroachDB containers.

## Security and maturity boundary

The current connector uses `NoTls` only in isolated local/CI database lanes. Production TLS verification, credential custody, pool sizing, cancellation, statement timeouts, observability, retry orchestration, failover and backup/restore remain mandatory work.

The adapter is synchronous by design for the first correctness slice. No throughput, async runtime, HA, durability-under-failure, migration compatibility, SG4 or production-ready claim is made.
