# Database and persisted-data machine denominators

Status: **candidate implementation; SG1, C2 and C3 remain open**  
Plan position: W0 `TG-W0-002`, W1 database foundation, W14 migration, `DEN-DB` and `DEN-DATA` / D6.

## Exact source contract

The generator consumes the complete, rehashed `heroiclabs/nakama` source tree at commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`, root tree `f3c9cfc2726d5543da1564629170f35b98e3797d`. It enumerates all `migrate/sql/*.sql` files in filename order and binds every leaf to exact path, Git blob, SHA-256 and line range.

The reviewed upstream migration tree currently contains 19 SQL files. Candidate generation still computes the count from the locked tree rather than trusting this document.

## SQL surface extraction

The standard-library lexer handles:

- `--` and nested `/* ... */` comments;
- single-quoted strings and doubled quotes;
- double-quoted identifiers;
- PostgreSQL dollar-quoted blocks;
- semicolon splitting only outside strings, comments and dollar quotes;
- `-- +migrate Up` and `-- +migrate Down` sections.

Candidate object extraction covers:

- migration files, sections and every SQL statement;
- tables, columns, table/inline constraints and indexes;
- `ALTER TABLE` actions;
- sequences, types, views, functions and triggers;
- drop operations, including multi-object drop lists;
- grants/revokes and transaction/control statements;
- inserts, updates and deletes as data-backfill candidates;
- primary/foreign/unique/not-null/check constraints as data-invariant candidates;
- defaults as separate data-default candidates rather than hard constraints.

Unsupported statements are retained under `manual_contracts`; they are never silently discarded.

## Compatibility boundary

The presence of an Up/Down pair does not prove a migration is safely reversible. Source parsing also does not prove PostgreSQL or CockroachDB execution semantics, online backfill safety, lock duration, query plans, data preservation, rolling compatibility, backup/PITR or rollback.

Every candidate leaf remains `unclassified`, `mandatory=null`, `planned` and `unreviewed`. Both manifests keep false claims for SG1, schema equivalence, data-semantic equivalence, migration compatibility, rollback and production readiness.

## Validation

- strings/comments/dollar-quote statement splitting;
- table/column/constraint/index/backfill extraction;
- multi-object drop enumeration;
- defaults separated from hard invariants;
- unknown statement fail-closed behavior;
- deterministic clean-directory outputs;
- exact source-lock post-fetch tamper rejection;
- SG1-negative gate;
- exact-head artifact workflow.

## Remaining work

- execute against the exact source tree and independently review all manual contracts;
- classify every D6 leaf and bind final owner/task/test/evidence;
- parse and verify complex alter-column/type/constraint actions in greater detail;
- execute every migration on pinned PostgreSQL and CockroachDB profiles;
- compare catalog objects, constraints, indexes and sequences after each version;
- prove expand/contract and rolling-version behavior;
- build large-table backfill, contention, retry and failure-injection corpora;
- prove backup, PITR, rollback barriers and corrupt-record handling;
- lock reviewed database and data-semantic digests.
