# Repository governance application

Status: reviewed source procedure; administrator execution and read-back evidence remain required.

## Purpose

`scripts/apply-repository-governance.sh` closes the code/procedure side of the main-protection blocker without weakening repository policy or configuring a required check that has never successfully existed.

The script is intentionally unable to:

- merge or approve a pull request;
- delete a branch;
- enable production, environment or release state;
- change secrets, deploy keys, packages or webhooks;
- treat an absent/skipped/cancelled check as success;
- apply protection to a drifting main SHA.

## Preconditions

1. GitHub CLI is authenticated as a repository administrator.
2. Repository identity is exactly `TrillionniumFoundation/TrillionniumGame`, ID `1323087470`.
3. `TRNM_EXPECTED_MAIN` is the exact current main commit.
4. Repository Actions are enabled.
5. That exact main commit already has a completed successful check named `trillionnium-game-merge-gate`.
6. The administrator supplies the explicit confirmation phrase.

The successful-check precondition prevents configuring an untested or unstable check name.

## Invocation

```bash
TRNM_GOVERNANCE_CONFIRM=apply-TrillionniumGame-main-governance-v1 \
TRNM_EXPECTED_MAIN=<exact-40-char-main-sha> \
bash scripts/apply-repository-governance.sh run/governance/main-protection
```

## Applied protection

- strict current-head required check: `trillionnium-game-merge-gate`;
- administrators subject to protection;
- at least one approving review;
- CODEOWNERS review;
- stale approval dismissal;
- latest push approval;
- linear history;
- no force push;
- no branch deletion;
- conversation resolution.

## Read-back evidence

The script records:

- repository identity before mutation;
- branch identity before mutation;
- exact-head check collection;
- Actions permission state;
- mutation request and response;
- branch protection and branch identity after mutation;
- structured result assertions;
- SHA-256 manifest of every artifact.

`GAP-P0-GOV-001` remains `blocked-external-admin` until this result is indexed and independently reviewed. If organization policy prevents Actions or protection, the script exits non-zero and retains the gap rather than weakening requirements.

## Claim boundary

Repository governance evidence may close SG0 governance criteria. It grants no Nakama wire/behavior/data/operations compatibility, production readiness, public-online approval or replacement credit.
