# TrillionniumGame documentation

Status: **authoritative current documentation**  
Revision: 2026-09-01

This is the only human documentation index for the active repository tree. Earlier design notes, dated progress snapshots, alpha documents, version-suffixed drafts and duplicate topic READMEs have been removed from the active tree. Their history remains available through Git, pull requests and issues; immutable machine evidence remains under `docs/evidence/`.

## Read in this order

1. [`../CURRENT_PLAN.md`](../CURRENT_PLAN.md) — complete scope, workstreams, stage gates and closure rules.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — current and target runtime, component boundaries and data flow.
3. [`DEVELOPMENT.md`](DEVELOPMENT.md) — repository layout, toolchains, local workflow and coding constraints.
4. [`COMPATIBILITY.md`](COMPATIBILITY.md) — pinned Nakama baseline, denominator, oracle and compatibility claims.
5. [`TESTING_AND_EVIDENCE.md`](TESTING_AND_EVIDENCE.md) — test classes, CI, artifact identity and independent review.
6. [`SECURITY_AND_PRIVACY.md`](SECURITY_AND_PRIVACY.md) — cryptography, keys, sessions, secrets, privacy and supply chain.
7. [`OPERATIONS_AND_RELEASE.md`](OPERATIONS_AND_RELEASE.md) — configuration, lifecycle, databases, observability, release and retirement.
8. [`GOVERNANCE.md`](GOVERNANCE.md) — branch policy, CODEOWNERS, required reviews and administrative acceptance.
9. [`ROADMAP.md`](ROADMAP.md) — current critical path, sequencing and parallel-development boundary.

## Machine authority

Human summaries never override machine state. Current execution and claims are derived from:

- [`status/CURRENT_STATE.json`](status/CURRENT_STATE.json)
- [`status/EXECUTION_STATUS.json`](status/EXECUTION_STATUS.json)
- [`status/GAP_REGISTER.json`](status/GAP_REGISTER.json)
- [`status/IMPLEMENTATION_INVENTORY.json`](status/IMPLEMENTATION_INVENTORY.json)
- [`status/PRODUCT_GATES.json`](status/PRODUCT_GATES.json)
- [`status/RISK_REGISTER.json`](status/RISK_REGISTER.json)
- [`roadmap/NEXT_MILESTONE.json`](roadmap/NEXT_MILESTONE.json)
- [`evidence/index.json`](evidence/index.json)
- [`development/PARITY_DENOMINATORS.json`](development/PARITY_DENOMINATORS.json)
- [`development/FEATURE_PARITY_MATRIX.md`](development/FEATURE_PARITY_MATRIX.md)
- [`DOCUMENTATION_AUTHORITY.json`](DOCUMENTATION_AUTHORITY.json)

The dated JSON files under evidence or status directories are records, not alternate current plans. An evidence item counts only when the evidence and gap validators accept its exact target identity and required independent review.

## Documentation change rule

A development change must update the smallest applicable current topic document and any affected machine state in the same pull request. Do not add a second document for the same topic. Do not create `V2`, `FINAL`, `NEW`, `ALPHA`, `CANDIDATE` or date-stamped Markdown files. Replace the current topic document and rely on Git history for prior versions.

Run:

```bash
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
```

The documentation authority check rejects undeclared Markdown under `docs/`, removed legacy directories, broken local links, stale repository-path references and reintroduced legacy naming patterns.

## Claim boundary

The consolidated documentation describes the intended system and the current source frontier. It does not assert complete Nakama compatibility, C1–C5, SG1–SG9, production readiness, public-online approval, drop-in replacement or Nakama retirement.
