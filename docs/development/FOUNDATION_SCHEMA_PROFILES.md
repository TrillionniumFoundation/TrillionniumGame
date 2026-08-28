# PostgreSQL and CockroachDB foundation schema profiles

Status: **DDL candidate; not executed**.

The two profiles intentionally use separate SQL files. They share the same logical tables and constraints, but PostgreSQL and CockroachDB are not represented as one implementation or one runtime compatibility claim.

## Atomicity boundary

`trnm_entity_heads`, `trnm_command_receipts`, `trnm_events`, `trnm_outbox`, and `trnm_command_outbox` must be mutated in one database transaction for a command commit. The future adapter must use row-count and uniqueness results to map stale revision/generation, duplicate command, event collision, and duplicate intent outcomes back to the Rust core.

## Session and storage boundary

Only token digests are persisted. Raw access or refresh tokens are forbidden. Session family revocation and consumed-token state are relational constraints. Storage objects preserve composite identity, ACL values, bounded payloads, and exact version digests.

## Profile separation

The candidate does not assume that transaction retries, lock behavior, schema changes, query plans, leaseholder movement, or failover are equivalent. Runtime test matrices remain separate.

## Rollback barrier

There is no automatic destructive down migration. Rollback requires a verified logical export, write fence, empty target rebuild, semantic comparison, and explicit approval. This protects acknowledged commands and session/storage data from accidental DROP-based rollback.

No database durability, migration compatibility, SG4, production, or public-online claim is made.
