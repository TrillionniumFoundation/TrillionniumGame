# Existing repository rename runbook

- Repository ID: `1323087470`
- Current name: `TrillionniumFoundation/Trillionnium-Nakama`
- Target name: `TrillionniumFoundation/TrillionniumGame`
- Original main archived at branch: `archive/trillionnium-nakama-main-2026-08-28-7f0d4be`
- Rename status: pending repository-settings mutation

## Current state

The TrillionniumGame planning tree has already been fast-forwarded onto the existing `main`. The original main commit remains an ancestor and is also preserved by the archive branch. Existing development branches, issues and pull requests were not deleted.

## Required rename operation

Rename the **same repository**. Do not delete it and create a replacement.

```bash
TRNM_REPOSITORY_RENAME_CONFIRM=rename-Trillionnium-Nakama-to-TrillionniumGame \
  bash scripts/rename-existing-repository.sh
```

The script verifies authenticated GitHub CLI access, source repository ID, target-name availability, current remote main, and explicit confirmation; performs the rename; then verifies repository ID, name and main SHA did not change.

## Pre-rename checklist

1. Record repository metadata, current main, all branches/tags, open PR/issues, rulesets, branch protection, workflows, environments, webhooks, deploy keys, package/container references and secret/variable names.
2. Ensure no merge or deployment is in progress.
3. Confirm target name is unoccupied.
4. Confirm archive branch points to `7f0d4be3e023aee86782bd3eb44a35f5dc991b15`.
5. Confirm exact current `main` contains the audited plan and no unreviewed implementation claim.

## Post-rename verification

- repository ID remains `1323087470`;
- full name is `TrillionniumFoundation/TrillionniumGame`;
- default branch and main commit are unchanged;
- all branches, tags, PRs, issues, releases, rulesets, environments and Actions history remain accessible;
- GitHub Apps, webhooks, deploy keys, packages, badges, component locks and sibling repositories use the canonical new URL;
- old URL redirect may be observed but is not a permanent dependency;
- planning checker is updated from `current_repository` old name to the new name in a reviewed follow-up commit;
- `GATE-REPOSITORY` remains open until immutable evidence is attached and independently reviewed.

## Failure policy

A failed rename must leave Git refs untouched. If GitHub returns a different repository ID or main SHA, stop all further mutation and preserve API responses as incident evidence. Repository deletion/recreation and force-push are prohibited.
