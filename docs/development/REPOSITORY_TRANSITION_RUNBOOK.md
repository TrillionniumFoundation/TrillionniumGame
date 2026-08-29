# Existing repository rename verification runbook

- Repository ID: `1323087470`
- Current name: `TrillionniumFoundation/TrillionniumGame`
- Previous name: `TrillionniumFoundation/Trillionnium-Nakama`
- Original main archived at branch: `archive/trillionnium-nakama-main-2026-08-28-7f0d4be`
- Rename status: completed on the existing repository

## Current state

The same GitHub repository now uses the canonical TrillionniumGame name and retains repository ID `1323087470`. The historical `Trillionnium-Nakama` name is transition evidence, not a second repository. The original main remains in ancestry and is explicitly preserved by the archive branch.

## Idempotent verification

The rename script now verifies an already-completed rename without mutating repository settings:

```bash
bash scripts/rename-existing-repository.sh
```

It confirms the canonical full name, repository ID and current main. The explicit mutation confirmation remains available only for disaster recovery against an unrenamed copy:

```bash
TRNM_REPOSITORY_RENAME_CONFIRM=rename-Trillionnium-Nakama-to-TrillionniumGame \
  bash scripts/rename-existing-repository.sh
```

## Post-rename verification

1. Confirm repository ID remains `1323087470`.
2. Confirm full name is `TrillionniumFoundation/TrillionniumGame`.
3. Confirm the original main and `archive/trillionnium-nakama-main-2026-08-28-7f0d4be` remain reachable.
4. Confirm branches, tags, pull-request records, issues, releases and workflow evidence remain accessible.
5. Confirm GitHub Apps, webhooks, deploy keys, packages, badges, component locks and sibling repositories use the canonical URL.
6. Treat any old-URL redirect as temporary compatibility behavior, never as a permanent dependency.
7. Keep `GATE-REPOSITORY` open until an independent reviewer accepts the governance snapshot.

## Failure policy

A verification failure must leave Git refs untouched. If GitHub reports another repository ID, unexpected full name or inaccessible ancestry, stop mutation and preserve API responses as incident evidence. Repository deletion/recreation and force-push remain prohibited.
