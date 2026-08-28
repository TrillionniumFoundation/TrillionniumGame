# Repository transition status

- Repository ID: `1323087470`
- Current GitHub name: `TrillionniumFoundation/Trillionnium-Nakama`
- Target GitHub name: `TrillionniumFoundation/TrillionniumGame`
- Original audited `main`: `7f0d4be3e023aee86782bd3eb44a35f5dc991b15`
- TrillionniumGame v1 baseline commit: `b5ac4e8c94c8652f25e4561075e7b3173217cf2f`
- History policy: retain all commits, branches, pull requests and issues; no force-push or delete/recreate
- Name mutation: **pending external repository-settings operation**

## Completed

1. Confirmed administrator/push access and repository identity.
2. Confirmed target repository name was not present in organization search at the time of planning.
3. Preserved existing Git history and all existing refs.
4. Fast-forwarded `main`; the original `main` remains an ancestor.
5. Replaced the project file identity with `trillionnium-game` and recorded current/target repository names explicitly.
6. Landed the full-Rust rewrite planning baseline and audit refinement as ordinary commits.

## Remaining administrative rename

The available GitHub automation connection can read and write repository contents, refs, issues and pull requests, but does not expose the repository-settings rename mutation. An organization administrator must rename the existing repository—not create a replacement—to `TrillionniumGame`.

Required verification after rename:

- repository ID remains `1323087470`;
- default branch and `main` commit/tree are unchanged;
- branches, tags, open PRs/issues, workflows, rulesets, environments and releases remain accessible;
- description/topics, webhooks, GitHub Apps, deploy keys, package/container links, badges, component locks and sibling repositories use the new canonical URL;
- the old URL redirect is observed but is not treated as a permanent integration mechanism;
- legacy Nakama implementation branches are labelled migration evidence and do not receive current release credit.

## Prohibited shortcut

Do not delete `Trillionnium-Nakama` and create a new repository with the target name. That would change the repository ID and break the history/evidence continuity required by SG0.
