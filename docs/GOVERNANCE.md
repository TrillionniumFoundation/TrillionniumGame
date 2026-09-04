# Governance

Status: **authoritative current documentation**  
Revision: 2026-09-05

## 1. Integration authority

`main` is the default integration branch. Product changes land through pull requests. Direct pushes, force pushes and deletion are prohibited by the desired policy. A branch-protection or ruleset claim is accepted only after GitHub state is read back with sufficient permissions and a negative rehearsal proves that missing checks/reviews block merge.

The repository currently exposes `main` as protected, but full ruleset/review enforcement has not been accepted by the machine governance contract. A desired-state document or successful workflow is not administrative proof.

## 2. Required aggregate

The stable required check is:

```text
trillionnium-game-merge-gate
```

It runs for every pull request and rejects missing, empty, zero-job, skipped, cancelled, neutral, timed-out, startup-failure, failed or older-head lanes. Expensive workflows may be path-aware, but the aggregate observes the complete required current-head collection and cannot report green while a mandatory family is absent or red.

Minimum source coverage includes documentation/control plane, workflow policy, root and isolated Rust workspaces, complete Python discovery, Go test/race/vet, schema authority and server contracts.

### Required workflow trigger contract

The closed required-workflow set applies to every candidate head, not only to changed paths. A mandatory `pull_request` workflow must not have `paths`, `paths-ignore`, `branches`, or `branches-ignore` selectors. Explicit activity types must include `opened`, `synchronize`, and `reopened`. Main-push selectors and all job bodies, permissions, test assertions and evidence requirements remain separate and unchanged. This intentionally trades additional CI execution for complete exact-head qualification; optimization requires an independently reviewed scope-aware evidence contract, never silently treating absent execution as success.

`python3 -m unittest tests.control_plane.test_required_workflow_source_contract -v` checks the real composed manifest against current workflow bytes, verifies every required PR trigger, and retains the full registered parent workflow set. The bounded trigger helper rejects ambiguous forms rather than acting as a general YAML parser; the existing workflow syntax policy remains mandatory. The immutable base manifest is not rewritten. Definition changes are bound through the existing digest-verified overlay, without changing workflow identity, removing required workflows, or granting old-head evidence credit.

GitHub's repository workflow catalog may temporarily expose the full registered path as its display name. The catalog identity helper permits only that exact form, and only when active ID/path, a successful current-head PR run, its canonical name, and the current regular source definition's Git blob all agree. Other renamed, disabled, missing, stale or substituted workflows reject. Receipts preserve the original observed catalog name. This does not relax job/assertion verification, rerun freshness, independent review or production gates.

## 3. Pull request state

Keep a pull request draft while any P0 blocker, stale identity, missing required result, unresolved review finding or unaccepted migration/security change remains.

The final description records:

- exact scope and accountable owner;
- task, gap, parity and gate IDs;
- base, head commit and head tree after the last source change;
- test and evidence results plus remaining work;
- migration, rollback, security and compatibility impact;
- limitations and forbidden claims.

A later source push invalidates prior exact-head evidence and stale review approval.

## 4. Independent review

Minimum desired policy:

- at least one approving reviewer other than the implementation author;
- stale approvals dismissed on head change;
- approval of the most recent push;
- CODEOWNERS approval for owned paths;
- all conversations resolved;
- administrators subject to the same restrictions except audited incident recovery.

Review expertise:

| Scope | Required expertise |
| --- | --- |
| migrations, schema and persistence | database/data integrity |
| token, crypto, secrets and identity | security/cryptography |
| HTTP, gRPC, generated API and RTAPI | protocol compatibility |
| presence, routing, match and realtime | distributed systems/realtime |
| workflows, evidence and release | CI/supply chain/program governance |
| runtime and module migration | runtime/sandbox/migration |
| Console and operator actions | application security/operations |

The active reviewer matrix is `docs/review/INDEPENDENT_REVIEW_MATRIX.json`. Empty roles and maintainer fallbacks do not satisfy independent evidence review.

Automation, the implementation author, generated manifests, summaries and comments cannot approve their own evidence. `COMMENTED`, requested review or an old-head approval is not accepted review.

## 5. CODEOWNERS

CODEOWNERS maps stable responsibility, not temporary availability. The target is independent organization teams for database, security, protocol, realtime, runtime, Console and SRE/release. A single global personal fallback is temporary and must not be treated as independent enforcement.

A change touching multiple trust domains requires every applicable owner class.

## 6. Merge freshness

The candidate must be current with `main` or pass a merge queue/prospective-merge check. Required workflows bind the expected head SHA and tree. A successful run on a pre-update base or prior head receives no credit.

Self-merge is prohibited when independent review is required. Administrator bypass and auto-merge cannot override unresolved P0/P1, missing evidence or stale identity.

## 7. Workflow governance

Workflow files use least privilege. Repository write permissions are absent from ordinary qualification workflows. External actions, when allowed, are pinned to verified immutable objects; repository-native actions/workflows are separately classified. Duplicate YAML keys and invalid workflow structures fail before merge.

Every job checks out the exact candidate explicitly. Job-log/artifact verification binds workflow/run/attempt/job and does not leak the repository token across redirect origins.

A temporary source publisher, self-mutating workflow, payload carrier or branch writer is not an acceptable final candidate. Final product bytes must be committed as ordinary reviewable source.

## 8. Evidence and state changes

Machine status is changed only to the highest state actually earned. Closing a gap requires all close criteria, exact implementation identity, non-empty successful tests, artifacts, accepted dependencies and required review. Editing a status JSON cannot create evidence.

Evidence is append-only in meaning. Invalid or expired evidence is marked accordingly rather than silently rewritten to accepted. Historical human narratives are removed from the live tree; immutable machine records remain audit inputs and are never alternate plans.

## 9. Documentation governance

The exact live Markdown set under `docs/` is defined by `docs/DOCUMENTATION_AUTHORITY.json`. Each topic has one current document. New version/date/final/candidate copies are forbidden. Replace the current document and use Git history for earlier content.

The documentation checker validates the allowlist, required markers, local links and repository path references. Deleting a legacy document requires migrating every active reference in scripts, workflows, JSON controls and root policies.

## 10. Branch lifecycle

Branch classes:

- `main` — protected integration authority;
- `feat/*`, `fix/*`, `docs/*`, `chore/*`, `codex/*` — active reviewed work;
- `integration/*` — temporary consolidation with one active line per scope;
- `archive/*` — immutable history/evidence;
- stale duplicate experiment branches — delete only after a reviewed before/after manifest.

Do not delete an active PR head, the only ref for unmerged work or an evidence-bearing archive. Branch cleanup records tip/tree, PR relationships, reachability and post-operation inventory.

## 11. Administrative desired state

The machine desired/readback inputs are:

- `docs/governance/MAIN_RULESET_DESIRED.json`;
- `docs/governance/REQUIRED_CHECKS.json`;
- `docs/governance/RULESET_DESIRED_ACTIVE_REQUEST.json`;
- `docs/governance/GITHUB_ADMIN_ACCEPTANCE.json`.

Administrative acceptance requires API/UI readback of direct/force-push blocking, required aggregate, approvals, stale dismissal, latest-push review, CODEOWNERS, conversation resolution, bypass actors and merge freshness. A negative pull request must be prevented from merging when any required condition is absent.

## 12. Emergency path

Emergency bypass is limited to active security/data-integrity containment and requires an incident commander, narrow scope, retained pre/post refs, safe tests, immediate normal-gate follow-up, independent post-incident review and proof protection was restored.

It cannot be used to grant C/SG, production, public-online, replacement or retirement authority.

## 13. Current governance blockers

Until accepted readback and independent ownership exist, the governance gaps remain open or externally blocked. The repository must not infer enforcement from a protected-branch boolean alone, and reviewers must bind decisions to the current exact candidate.
