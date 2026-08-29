# PostgreSQL and CockroachDB foundation schema profiles

Status: **single-node runtime verified; durability, migration and HA not yet verified**.

The two profiles intentionally use separate SQL files. They share the same logical tables and constraints, but PostgreSQL and CockroachDB are not represented as one implementation or one runtime compatibility claim.

## Runtime evidence

Exact DDL commit `e9b63462fa91383b06706894afed31b378f6b48c` was executed in relay run `33167200252` against pinned fresh containers:

- PostgreSQL `16.4` from `postgres:16.4-bookworm`;
- CockroachDB `v24.1.2` from `cockroachdb/cockroach:v24.1.2`.

Both profiles passed:

- fresh migration application;
- exact introspection of the ten expected `trnm_*` tables;
- one transaction spanning entity head, command receipt, event, outbox, command/outbox ordering, authority lease, session family, refresh-token digest and storage object state;
- rejection of an invalid one-byte entity identity;
- an intentionally failed transaction whose temporary entity remained absent (`visible row count = 0`).

The tested SQL blobs remain independently identified as PostgreSQL `07f5f4923d884cc63bf53074096b8d1e04215096` and CockroachDB `b836b8a2f025ef22525e9e5f089db01ab5f06fe6`.

## Atomicity boundary

`trnm_entity_heads`, `trnm_command_receipts`, `trnm_events`, `trnm_outbox`, and `trnm_command_outbox` must be mutated in one database transaction for a command commit. The future adapter must use row-count and uniqueness results to map stale revision/generation, duplicate command, event collision, and duplicate intent outcomes back to the Rust core.

The runtime matrix proves database transaction rollback for one constrained failure case. It does not yet prove adapter retry correctness, acknowledged-command durability under process/host failure, or distributed failover behavior.

## Session and storage boundary

Only token digests are persisted. Raw access or refresh tokens are forbidden. Session family revocation and consumed-token state are relational constraints. Storage objects preserve composite identity, ACL values, bounded payloads, and exact version digests.

## Profile separation

The current evidence is single-node and profile-specific. It does not assume that transaction retries, lock behavior, schema changes, query plans, leaseholder movement, backup/restore, or failover are equivalent.

## Rollback barrier

There is no automatic destructive down migration. Rollback requires a verified logical export, write fence, empty target rebuild, semantic comparison, and explicit approval. This protects acknowledged commands and session/storage data from accidental DROP-based rollback.

No database durability, migration compatibility, HA, SG4, production, or public-online claim is made.
