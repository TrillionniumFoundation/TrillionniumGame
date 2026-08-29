# Branch and merge policy

Status: binding desired repository policy for plan v3. Repository settings must be read back and evidenced before `GAP-P0-GOV-001` is closed.

## 1. Protected target

`main` is the only default integration branch. Direct pushes, force pushes and deletions are prohibited. Changes land through pull requests and squash or rebase according to repository settings; merge commits remain disabled unless an ADR changes the policy.

## 2. Required aggregate check

Every pull request must run a stable aggregate check named:

```text
trillionnium-game-merge-gate
```

The aggregate check runs for every pull request regardless of path and fails when an affected mandatory lane is absent, skipped, cancelled, empty or executed against another commit. Expensive jobs may use path-aware planning, but the aggregate job must validate the plan and collected evidence.

Minimum source lanes:

- plan/status/gap/evidence validation;
- root Rust workspace format, test and strict Clippy;
- standalone security-critical Rust adapter format/test/strict Clippy;
- Python contract tests;
- Go test/race/vet for current migration inputs;
- schema-authority and forbidden-consumer checks;
- workflow/action pin and repository identity checks.

Live database, differential, fault, load and security lanes become required for the relevant task/gate and are validated through exact-head evidence manifests.

## 3. Required reviews

Minimum repository setting:

- at least one approving review from a person other than the latest substantive implementer;
- dismiss stale approvals when the head changes;
- require review of the most recent push;
- require CODEOWNERS review for owned paths;
- require all review threads resolved;
- administrators follow the same merge restrictions except documented emergency recovery.

Path review requirements:

| Paths | Required expertise |
| --- | --- |
| `migrations/**`, `database/**`, `crates/trnm-persistence-*/**` | database/data-integrity |
| `crates/trnm-token-*/**`, `docs/security/**`, `SECURITY.md` | security/cryptography |
| protocol/generated/transport/RTAPI paths | protocol compatibility |
| presence/realtime/match ownership paths | distributed systems/realtime |
| workflows, evidence, gates and release scripts | CI/supply-chain/program governance |

The initial CODEOWNERS file may contain a temporary maintainer fallback because named teams are not yet available. That fallback does not satisfy independent-review gates; `GAP-P1-REVIEW-001` remains open until named users/teams and evidence decisions exist.

## 4. Merge freshness

The PR must be up to date with `main` or pass through a merge queue that tests the prospective merge commit. A successful run for an older head or pre-update merge base is not sufficient. Required checks are bound to the expected head SHA during merge.

## 5. Required conversation and metadata

A PR description includes:

- exact scope and accountable owner;
- gap/task/parity/gate IDs;
- exact candidate commit/tree after the final push;
- tests executed and tests still required;
- migration/rollback/compatibility/security effects;
- evidence IDs/artifacts or an explicit statement that no claim credit is requested;
- residual limitations and forbidden claims.

Draft is required while P0 blockers, missing required tests or known stale candidate identity remain. Labels/status text do not override gates.

## 6. Commit and provenance

- repository web commit signoff remains enabled;
- releases and production artifacts require signed provenance;
- actions use immutable commit SHAs;
- container images use immutable digests for evidence/release lanes;
- dependency lockfiles and generated source identities are reviewed;
- workflow changes receive supply-chain/governance review.

Unsigned historical commits are not rewritten solely to add signatures, because preserving history is part of the audit chain. New release provenance starts from the enforced policy activation point.

## 7. Emergency changes

Emergency bypass is limited to containing an active security/data-integrity incident. Requirements:

1. named incident commander and reason;
2. minimal change with no unrelated feature work;
3. retained pre/post refs and artifacts;
4. tests that can safely run before deployment;
5. immediate follow-up PR through the normal gate;
6. independent post-incident review;
7. evidence of restored protection settings.

An emergency bypass cannot be used to promote compatibility, production or replacement claims.

## 8. Branch lifecycle

Branches are classified:

- `main` — protected integration authority;
- `feat/*`, `fix/*`, `docs/*`, `chore/*` — active reviewed work;
- `integration/*` — temporary consolidation only, with one active line per scope;
- `archive/*` — immutable historical refs;
- duplicated/stale experiment branches — delete only after an approved disposition manifest and ref evidence.

Closing a PR does not automatically delete an archive or evidence-bearing branch. Active PR heads are retained until superseded/closed and recorded.

## 9. Settings acceptance checklist

`GAP-P0-GOV-001` closes only after API/UI read-back proves:

- `main` protection/ruleset enabled;
- direct and force pushes blocked;
- `trillionnium-game-merge-gate` required;
- required approvals and stale-dismissal enabled;
- CODEOWNERS review enabled;
- conversation resolution and latest-head freshness enabled;
- bypass actors documented;
- a test PR is prevented from merging when the check or review is absent.

The policy document and workflow source are preparation, not proof that the settings are active.
