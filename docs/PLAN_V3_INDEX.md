# TrillionniumGame plan v3 execution index

This index is navigation only. Machine state and evidence remain authoritative in the referenced JSON contracts.

## Mission and boundary

- `CURRENT_PLAN.md`
- `PROJECT_BOUNDARY.md`
- `PROJECT_BOUNDARY.json`
- `docs/adr/ADR-0001-FULL-RUST-REIMPLEMENTATION.md`
- `docs/development/COMPATIBILITY_PROFILES.md`
- `docs/development/COMPATIBILITY_PROFILES.json`

## Current execution truth

- `docs/status/CURRENT_STATE.json`
- `docs/status/EXECUTION_STATUS.json`
- `docs/status/GAP_REGISTER.json`
- `docs/status/IMPLEMENTATION_INVENTORY.json`
- `docs/status/PRODUCT_GATES.json`
- `docs/status/RISK_REGISTER.json`
- `docs/roadmap/NEXT_MILESTONE.json`

## Scope and upstream truth

- `docs/development/UPSTREAM_BASELINE.json`
- `docs/development/PARITY_DENOMINATOR_SPEC.md`
- `docs/development/PARITY_DENOMINATORS.json`
- `docs/development/FEATURE_PARITY_MATRIX.md`
- `docs/development/EXECUTION_BACKLOG.json`
- `docs/development/backlog/EXECUTION_BACKLOG.v2.json.gz`
- `docs/development/COMPATIBILITY_DIVERGENCES.json`

## Architecture and first vertical slice

- `docs/architecture/CURRENT_AND_TARGET_RUNTIME.md`
- `docs/architecture/RUST_SERVER_REFERENCE_ARCHITECTURE.md`
- `docs/development/RUST_SERVER_VERTICAL_SLICE_ALPHA.md`
- `docs/development/PGWIRE_VERTICAL_SLICE_COMMAND.md`
- `docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json`
- `crates/trnm-persistence-core/src/bin/trnm-server.rs`
- `crates/trnm-persistence-pg/src/bin/trnm-pg-command.rs`

## Database authority and recovery

- `docs/development/SCHEMA_AUTHORITY.json`
- `migrations/postgresql/`
- `migrations/cockroachdb/`
- `database/schema/v2/STATUS.json`
- `docs/development/DATABASE_BACKUP_RESTORE_CONTRACT.md`
- `scripts/check-schema-authority.py`

## Evidence and compatibility

- `docs/development/EVIDENCE_MODEL.md`
- `docs/evidence/index.json`
- `docs/evidence/INDEX_CONTRACT.md`
- `docs/evidence/schemas/trillionnium-evidence-v1.schema.json`
- `scripts/check-evidence-index.py`
- `scripts/derive-gap-status.py`
- `scripts/derive-gates.py`

## Security

- `SECURITY.md`
- `docs/security/CRYPTOGRAPHY_AND_KEYS.md`
- `crates/trnm-token-jwt-adapter/`
- `crates/trnm-token-jwt-adapter/tests/security_vectors.rs`

## Testing

- `docs/testing/TEST_POLICY.md`
- `.github/workflows/trillionnium-game-merge-gate.yml`
- `scripts/check-plan.py`
- `scripts/check-status-transitions.py`
- `scripts/check-rust-server-slice.py`
- `scripts/check-independent-review-matrix.py`
- `tests/control_plane/`

## Governance and independent review

- `.github/CODEOWNERS`
- `docs/governance/BRANCH_AND_MERGE_POLICY.md`
- `docs/governance/GITHUB_ADMIN_ACTIVATION_RUNBOOK.md`
- `docs/governance/MAIN_RULESET_DESIRED.json`
- `docs/governance/BRANCH_CONSOLIDATION_2026-08-29.md`
- `docs/review/INDEPENDENT_REVIEW_MATRIX.json`

## Claim boundary

This index does not state that any listed implementation, workflow, reviewer assignment, administrator setting or evidence item is accepted. The current claim is derived only from exact candidate evidence and remains fail closed whenever checks, artifacts, external settings or independent review are absent.