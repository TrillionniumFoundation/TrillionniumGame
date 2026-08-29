# GitHub governance state read-back

Status: binding observation procedure for `GAP-P0-CI-001` and `GAP-P0-GOV-001`.

## Purpose

`scripts/check-github-governance-state.py` reads the repository state after every administrator action. It prevents issue comments, screenshots, workflow files or an unverified settings change from closing the repository-control gaps.

## Invocation

```bash
gh auth status
python3 scripts/check-github-governance-state.py \
  --head <exact-candidate-or-main-sha> \
  --output run/governance/github-state.json
```

The caller must use credentials able to read Actions permissions and main protection.

## CI gap closes only when

- repository Actions are enabled;
- the exact observed head has a non-empty check collection;
- both stable aggregate checks have completed successfully:
  - `trillionnium-game-merge-gate`;
  - `v3-source-and-scope-gate`.

A workflow file, a run against another SHA, a skipped/cancelled run or a successful relay does not close the gap.

## Governance gap closes only when

- main is reported protected;
- both stable aggregate contexts are strict required checks;
- administrators are enforced;
- stale approvals are dismissed;
- CODEOWNERS review is required;
- approval of the latest push is required.

The v2 application script additionally enforces no force push, no deletion, linear history and conversation resolution. The read-back artifact and application artifact are both indexed before closure.

## Claim boundary

Repository-control readiness is a prerequisite for trusted engineering evidence. It does not grant compatibility, production readiness or public-online status.
