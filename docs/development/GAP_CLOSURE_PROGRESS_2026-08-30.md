# Plan v3 gap-closure progress — 2026-08-30

Status: source changes in Draft PR #42; exact-head target-repository execution and independent review remain mandatory.

## Completed in the source line

### Control-plane deepening

- Added machine validation for the gap register, evidence index, migration lock and GitHub administrator acceptance contract.
- Added control-plane unit tests for gap closure, evidence credit, migration identity and governance observation rejection.
- Bound all new validators into the aggregate merge gate.
- Added a plan-v3 extension checker so future edits cannot silently remove current-state, gap, evidence, schema, security, governance or vertical-slice artifacts.

### Database authority

- Added `migrations/MIGRATION_CHAIN.lock.json` with a complete ordered inventory and Git blob identity for PostgreSQL and CockroachDB.
- Added `scripts/check-migration-lock.py`, which rejects unlisted SQL, missing files, duplicate paths, profile leakage and blob drift, and emits a separate chain SHA-256 for each profile.
- Bound `SCHEMA_AUTHORITY.json` to the lock while preserving the rule that source identity is not runtime, migration, restore, PITR or HA evidence.

### Rust server vertical slice

- Added the first `trnm-server` Rust binary as a mandatory standalone workspace.
- Implemented typed config, nonzero limits, bounded request-head reads, I/O deadlines, health, readiness, loopback drain and a bounded accept loop.
- Added explicit 501/426 negative boundaries for command and realtime adapters instead of simulating unverified behavior.
- Added source tests for configuration, readiness, oversized ingress, stable negative behavior, drain and pre-requested shutdown.
- Added a fail-closed status artifact and a detailed VS1–VS5 closure matrix.

### Repository administration

- Added a desired-state JSON contract for Actions, selected immutable action pins, main protection, required aggregate check and independent domain reviewers.
- Added an authenticated live read-back verifier that hashes GitHub API responses and rejects any false governance fact.
- Added the exact administrator runbook; it explicitly forbids switching to unrestricted actions merely to obtain a green check.

## Previously fixed source defects retained

- JWT constant-time length comparison no longer truncates a length difference to eight bits.
- Outbox retry exhaustion transitions atomically to a stable dead-letter state.
- Required live database tests fail when prerequisites are absent; local skip remains no-credit.
- Runtime Go module identity uses the renamed repository.
- The incompatible `database/schema/v2` family remains quarantined from runtime, active CI, backup and release consumers.

## Not closed by source presence

The following remain open or blocked until their close criteria are actually evidenced:

- non-empty successful exact-head target-repository Actions and aggregate check;
- active main ruleset/branch protection read-back;
- named independent database, security, protocol and realtime reviewers;
- current-head PostgreSQL/CockroachDB live fault, restore, PITR and failover evidence;
- independent crypto review or approved library replacement;
- full authenticated HTTP/gRPC command path;
- WebSocket JSON/protobuf path and reconnect/revocation behavior;
- database-backed receipt/event/outbox composition and acknowledgement-after-commit;
- process restart and stale-worker recovery;
- immutable Nakama differential;
- D0–D8 zero-unclassified denominator lock;
- all remaining domain, migration, security, load, HA, canary and retirement gaps.

## Claim boundary

This progress record does not change C0–C5, SG0–SG9, production, public-online, drop-in or retirement truth. A source-level fix can advance a row only to `source-candidate` until the exact candidate runs, artifacts are indexed and the required independent reviewer accepts the evidence.
