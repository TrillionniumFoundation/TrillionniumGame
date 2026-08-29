# Foundation database runtime evidence v2

Date: 2026-08-28  
Target commit: `e9b63462fa91383b06706894afed31b378f6b48c`  
Relay run: `33167200252`

## Result

Both fresh single-node profiles passed the complete runtime matrix.

| Profile | Runtime | Job | Artifact | Digest |
|---|---|---:|---:|---|
| PostgreSQL | 16.4 (`postgres:16.4-bookworm`) | `98835331955` | `9684061687` | `sha256:1107dc166a15b1806a60e3ba7dd50701207c8e10c56e18d909ddec9d18063b71` |
| CockroachDB | v24.1.2 (`cockroachdb/cockroach:v24.1.2`) | `98835331779` | `9684061933` | `sha256:437b231e06ed8d5ab8309a433a202c6dbde2031c2c2d8ad2876e016329b308b3` |

The tested DDL blobs were:

- PostgreSQL: `07f5f4923d884cc63bf53074096b8d1e04215096`;
- CockroachDB: `b836b8a2f025ef22525e9e5f089db01ab5f06fe6`.

## Matrix

Each profile completed all of the following with status `0`:

1. pinned image pull and container start;
2. real SQL readiness, not process-only readiness;
3. fresh migration apply;
4. introspection of exactly ten expected `trnm_*` tables;
5. one transaction writing entity, command receipt, event, outbox, command/outbox order, authority lease, session family, refresh-token digest and storage object state;
6. rejection of an invalid one-byte entity identifier;
7. an intentionally invalid command-receipt transaction followed by a visibility query proving the temporary entity row count remained `0`.

## Scope boundary

This closes the previous “DDL candidate unexecuted” gap and proves single-node execution plus one failed-transaction atomicity case. It does not yet prove:

- the Rust database adapter or error mapping;
- serializable retry correctness;
- acknowledged-command durability under process, host or storage failure;
- Cockroach leaseholder movement or multi-node failover;
- PostgreSQL replication/failover;
- backup, PITR, logical export/rebuild rollback or migration compatibility;
- SG4, production readiness or public-online status.

The machine-readable companion is `2026-08-28-foundation-database-runtime-v2.json`.
