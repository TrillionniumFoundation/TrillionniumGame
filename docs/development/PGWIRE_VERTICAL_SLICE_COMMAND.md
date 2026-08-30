# PG-wire vertical-slice command

Status: source candidate; no database, migration, compatibility or production claim.

Source:

```text
crates/trnm-persistence-pg/src/bin/trnm-pg-command.rs
```

## Purpose

This one-shot binary binds the first Rust composition slice to the existing `PgRepository` without pretending that a synchronous single-client command tool is the final server architecture. It provides an executable seam for exact-head PostgreSQL and CockroachDB transaction/fault evidence.

## Required environment

```text
TRNM_DATABASE_URL
TRNM_DATABASE_PROFILE=postgresql|cockroachdb
TRNM_SCHEMA_SOURCE_COMMIT=<40 hexadecimal characters>
TRNM_SCHEMA_APPLIED_AT_MS=<u64>
```

The database must already contain the corresponding production-authoritative migration from `migrations/<profile>/`. The binary never creates or mutates schema implicitly.

## Commands

- `bootstrap`: create one entity head with a non-zero authority generation;
- `head`: read the current revision, event sequence and authority generation;
- `apply`: commit one command receipt, one event and one outbox intent in a SERIALIZABLE transaction;
- exact duplicate `apply`: replay the stored receipt;
- changed fingerprint, stale revision or stale generation: fail closed through the stable domain error.

All successful JSON output includes `compatibility_credit=false`.

## Required exact-head evidence

For each database profile:

1. fresh migration apply and catalog digest;
2. schema metadata bound to exact candidate commit/tree and migration-chain digest;
3. bootstrap;
4. first apply;
5. exact duplicate replay;
6. changed-fingerprint rejection;
7. stale revision rejection;
8. stale authority generation rejection;
9. failure at receipt, event and outbox insertion with zero partial commit;
10. commit-success/response-loss reconnect and exact receipt replay;
11. SQLSTATE `40001` retry-driver proof;
12. process restart;
13. backup to empty restore and semantic comparison.

PostgreSQL and CockroachDB require separate artifacts and conclusions.

## Explicit limitations

The source candidate currently uses the synchronous repository client and has no accepted connection pool, TLS profile, request cancellation, total deadline, bounded retry driver, lease-expiry/reclaim worker, HA/failover or PITR evidence. It cannot close `GAP-P1-PG-001`, `GAP-P0-DATA-001`, `GAP-P0-SERVER-001`, SG4, C2, C3 or C4 by itself.