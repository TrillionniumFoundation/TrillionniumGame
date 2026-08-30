# GitHub administrator activation runbook

Status: required external-administrator procedure. The presence of this file does not prove any setting is active.

## Scope

Repository:

```text
TrillionniumFoundation/TrillionniumGame
repository_id=1323087470
```

Affected gaps:

```text
GAP-P0-CI-001
GAP-P0-GOV-001
GAP-P1-REVIEW-001
```

The administrator must retain the raw request/response payloads, actor identity, timestamps and resulting exact-head run IDs. Screenshots alone are not accepted evidence.

## Phase 1 — read-only preflight

Record and review:

1. repository Actions permissions;
2. organization Actions permissions and allowed-actions policy;
3. runner/billing availability;
4. workflow approval policy for organization-owned branches and pull requests;
5. GitHub App workflow permissions;
6. current branch protection and rulesets;
7. current `main` SHA/tree;
8. current PR #42 SHA/tree;
9. current workflow/check-run collections for both identities.

Acceptance before mutation:

- repository ID is still `1323087470`;
- default branch is `main`;
- no existing stronger ruleset will be weakened;
- immutable action SHAs used by the aggregate workflow are explicitly allowed;
- at least one independent reviewer/team is available before a review requirement becomes blocking.

## Phase 2 — enable exact-head Actions

Enable Actions for the repository without allowing mutable unreviewed actions. The required workflow is:

```text
.github/workflows/trillionnium-game-merge-gate.yml
```

Allowed external actions must be pinned to reviewed commits. At the current contract boundary these include the exact commits referenced in the workflow for:

```text
actions/checkout
actions/setup-go
```

Do not replace immutable commits with floating tags.

Trigger the aggregate workflow against the exact current PR #42 head. If the head changes during execution, the run is diagnostic only and must be repeated.

Required first-run evidence:

- repository, workflow, event and requested ref;
- checked-out commit and tree;
- run ID and attempt;
- every job ID and conclusion;
- each step conclusion;
- artifact IDs, names, sizes and digests;
- non-empty assertion/test counts;
- exact limitations;
- terminal aggregate `trillionnium-game-merge-gate` conclusion.

Empty, absent, skipped, cancelled, stale-head, neutral or timed-out collections do not pass.

## Phase 3 — stabilize the required check

Only after at least one successful exact-head run:

1. verify the final check-run name is exactly `trillionnium-game-merge-gate`;
2. rerun after a harmless reviewed change and confirm the name is stable;
3. confirm the aggregate job fails when a required child job is skipped or failed;
4. confirm the run checks the PR head rather than an unrelated merge or base identity;
5. record the stable check name and workflow blob in evidence.

## Phase 4 — activate the `main` ruleset

The active policy must provide at least:

- pull request required for every change;
- direct update and force push blocked;
- branch deletion blocked;
- `trillionnium-game-merge-gate` required;
- branch must be current with `main` before merge or must use merge queue;
- at least one approval from an independent reviewer;
- CODEOWNERS approval for protected paths;
- approval dismissed after new commits;
- approval of the most recent push by someone other than its author;
- all conversations resolved;
- linear history using squash or rebase according to repository policy;
- administrators included unless a separately approved emergency bypass exists.

Do not activate a required reviewer rule that only the implementation author can satisfy. Assign the independent reviewer/team first.

## Phase 5 — post-mutation readback

Read the repository/ruleset/branch APIs again and prove:

- the ruleset is active and targets `refs/heads/main`;
- required checks include the stable aggregate name;
- direct push, force-push and deletion bypasses are absent or explicitly governed;
- review count and stale-approval dismissal are active;
- CODEOWNERS is recognized;
- the current `main` commit/tree did not change as a side effect;
- exact-head PR runs continue to execute.

Create one evidence manifest under `docs/evidence/` and index it. The reviewer must not be the administrator who performed the mutation.

## Rollback

If the policy unexpectedly blocks all maintainers or the required check cannot run:

1. preserve all failed API responses and check collections;
2. do not push directly to `main` as a workaround;
3. disable only the newly introduced blocking rule through an audited administrator action;
4. restore the previous ruleset snapshot;
5. keep Actions enabled if workflows themselves are safe;
6. reopen the governance gap and record the failure cause.

No rollback may weaken existing security policy below the preflight snapshot.

## Closure

`GAP-P0-CI-001` and `GAP-P0-GOV-001` close only after exact API readback, successful exact-head execution, indexed artifacts and accepted independent review. Committing a workflow, CODEOWNERS file, desired policy JSON or this runbook does not close either gap.