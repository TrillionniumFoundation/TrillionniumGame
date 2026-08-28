# Repository transition status

- Repository ID: `1323087470`
- Current GitHub name: `TrillionniumFoundation/Trillionnium-Nakama`
- Target GitHub name: `TrillionniumFoundation/TrillionniumGame`
- Original audited `main`: `7f0d4be3e023aee86782bd3eb44a35f5dc991b15`
- Archive branch: `archive/trillionnium-nakama-main-2026-08-28-7f0d4be`
- TrillionniumGame v1 baseline commit: `b5ac4e8c94c8652f25e4561075e7b3173217cf2f`
- TrillionniumGame v2 audit commit: `4e7ea722d2271b4f400c108265204d02ad4fc69b`
- History policy: retain all commits, branches, pull requests and issues; no force-push or delete/recreate
- Name mutation: **pending external repository-settings operation**

## Completed

1. Confirmed administrator/push access and repository identity.
2. Confirmed target repository name was not present at the beginning of the transition.
3. Preserved existing Git history and development refs.
4. Created an explicit archive branch for the original `main`.
5. Fast-forwarded `main`; the original main remains an ancestor.
6. Replaced project file identity with `trillionnium-game` and recorded current/target names explicitly.
7. Landed the full-Rust rewrite baseline, audit refinement and upstream-identity correction as ordinary commits.

## Remaining administrative rename

The available GitHub automation connection can read and write repository contents, refs, issues and pull requests, but does not expose the repository-settings rename mutation. An organization administrator must rename the existing repository—not create a replacement—to `TrillionniumGame` using `scripts/rename-existing-repository.sh` or the GitHub repository settings page.

Close `GATE-REPOSITORY` only after evidence proves:

- repository ID remains `1323087470`;
- full name is `TrillionniumFoundation/TrillionniumGame`;
- default branch and current `main` commit/tree are unchanged across rename;
- branches, tags, issues, PRs, workflows, rulesets, environments and releases remain accessible;
- integrations and component locks use the new canonical URL.

## Prohibited shortcut

Do not delete `Trillionnium-Nakama` and create a new repository with the target name. That would change the repository ID and break history and evidence continuity.
