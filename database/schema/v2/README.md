# `database/schema/v2` — non-authoritative design history

This directory is **not** a production migration source and earns no database, migration, compatibility, backup, restore or release credit.

The production-authoritative schema chain is:

- `migrations/postgresql/`
- `migrations/cockroachdb/`
- SQL ABI: `crates/trnm-persistence-pg`
- authority contract: `docs/development/SCHEMA_AUTHORITY.json`

The files retained here explore a materially different tenant, UUID and timestamp model. They may be used only for human design comparison and historical differential analysis. Runtime code, live CI, migration runners, backup/restore scripts and deployment tooling must not consume them.

Promoting any part of this design requires an approved ADR, expand/contract migration, adapter update, semantic data migration, rollback barrier, both database profile evidence and independent database review. Editing this README or `STATUS.json` cannot perform that promotion.
