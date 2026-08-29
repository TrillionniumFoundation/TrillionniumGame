# Audit index

## Current audit control surfaces

- [`../status/GAP_REGISTER.json`](../status/GAP_REGISTER.json) — current P0/P1/P2 findings, owners, blockers and close criteria.
- [`../development/COMPATIBILITY_DIVERGENCES.json`](../development/COMPATIBILITY_DIVERGENCES.json) — known baseline/candidate behavioral differences.
- [`../status/RISK_REGISTER.json`](../status/RISK_REGISTER.json) — operational, security, compatibility and program risks.
- [`../status/IMPLEMENTATION_INVENTORY.json`](../status/IMPLEMENTATION_INVENTORY.json) — source-level implementation inventory and missing evidence.
- [`../evidence/index.json`](../evidence/index.json) — evidence validity, target identity, review and expiry boundary.
- [`../development/PLAN_AUDIT_2026-08-28.md`](../development/PLAN_AUDIT_2026-08-28.md) — original plan-v2 audit history.

A finding is not closed by a document edit, source commit, local test, workflow definition or issue state alone. Plan v3 requires exact candidate identity, non-empty required execution, artifacts, accepted evidence and independent review for P0/P1 closure.

## Historical tranche records

`WORLD_COMMAND_DEPLOYED_RUNTIME_RISK_REGISTER_V1.md` and similar files remain valid only for their explicitly named historical slices. They do not replace the current gap, divergence, risk or evidence registers.

Trillionnium Chain remains outside this audit tranche and repository boundary.
