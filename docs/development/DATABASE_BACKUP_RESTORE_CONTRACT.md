# Database backup and semantic restore contract

Status: implemented harness; production PITR and multi-node recovery remain open.

## Profiles

The harness runs two separate profiles and does not infer parity between them:

- PostgreSQL 17.6: `pg_dump -Fc` into an immutable evidence file, restore into a newly created empty database with `pg_restore`, then semantic comparison.
- CockroachDB 24.1.2: native `BACKUP DATABASE` into node-local external storage, `RESTORE DATABASE ... WITH new_db_name`, then semantic comparison.

Both images are fixed by reviewed digest. The CockroachDB platform image ID is also checked before execution.

## Seed corpus

The source database contains deterministic data across all ten foundation tables:

- entity heads;
- command receipts;
- events;
- outbox intents;
- command/outbox links;
- authority leases;
- session families;
- refresh-token digests;
- storage objects;
- schema metadata.

The source includes successful commands, transaction rollback fixtures, response-loss replay fixtures and restart-recovery fixtures.

## Semantic comparison

For each table, the harness emits CSV ordered by its complete primary-key order. It prefixes table boundaries, hashes both complete snapshots and requires byte-for-byte equality with `cmp` after restoration into an empty database.

A successful backup command, matching row counts or schema-only restore is insufficient.

## Evidence and limits

A passing run may claim:

```text
backup_created = true
empty_restore = true
semantic_snapshot_equal = true
```

It must keep these false:

```text
production_pitr = false
multi_node_restore = false
```

Production PITR/RPO/RTO, encrypted remote object storage, primary or leaseholder failover, disk exhaustion, multi-node restore and independent SRE approval remain separate Issue #36 gates.
