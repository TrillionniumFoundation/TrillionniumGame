# Branch disposition contract

Status: binding plan-v3 governance contract.

## Objective

Reduce competing source authority without deleting unique or non-reachable history. Branch count is not itself a release metric; cleanup is allowed only after an exact before inventory, independent review and an exact after verification.

## Inventory

`scripts/generate-branch-inventory.py` fetches every remote head and records:

- branch name;
- exact tip commit and tree;
- whether the tip is reachable from main;
- whether main is reachable from the tip;
- all branch names sharing the same tip;
- all branches sharing the same content tree;
- a non-destructive proposed disposition.

The generator never deletes refs or rewrites history.

## Dispositions

```text
keep                         canonical main
keep-active                  current reviewed implementation line
keep-archive                 explicit immutable archive namespace
preserve-pending-review      known unique source still awaiting disposition
preserve-nonancestor         not reachable from main; deletion forbidden
archive-or-delete-after-review
                             reachable unique name; reviewer decides
 delete-candidate-after-review
                             reachable duplicate tip; still requires review
```

## Deletion gate

A branch may be deleted only when:

1. its exact tip appears in the reviewed before manifest;
2. the tip is reachable from main or an immutable archive ref;
3. no open PR, release, deployment, evidence or package depends on the name;
4. no unique source or tree has been silently discarded;
5. an independent governance reviewer accepts the row disposition;
6. the deletion command is bounded to approved names;
7. an after manifest proves every preserved tip and the exact main identity;
8. evidence is indexed with artifact digests.

Non-ancestor tips cannot be deleted by the ordinary cleanup path.

## Current special cases

- `feat/plan-v3-gap-closure-2026-08-29` remains the active implementation line while PR #42 is open.
- `integration/all-branches-main-v1` is retained after PR #41 closure because it contains five files not present on the audited main; those files require focused review rather than implicit merge or deletion.
- `archive/trillionnium-nakama-main-2026-08-28-7f0d4be` remains an explicit historical archive.

## Claim boundary

An inventory artifact does not mean cleanup is complete. Branch cleanup cannot earn Nakama compatibility, SG1-SG9, C1-C5, production readiness or public-online approval.
