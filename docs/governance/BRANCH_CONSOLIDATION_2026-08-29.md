# Branch consolidation disposition — 2026-08-29

Status: reviewed disposition candidate, not a deletion authorization. Source observation began from `main@326e670cb008a990247e31a63c0c4b0e338df62f`. Re-read all refs immediately before any mutation.

The previous version of this document described a future single-branch state as though it were already frozen and referenced a different `main` identity. Plan v3 replaces that unsafe posture with an evidence-first disposition process.

## 1. Rules

1. Never delete `main`, an active PR head, an evidence-bearing archive, or the only ref for an unmerged commit.
2. Record branch name, tip SHA, tree SHA, open/closed PR references and ancestor relation to the accepted `main` candidate before deletion.
3. Equal tip SHA does not automatically mean equal governance purpose; archive refs remain immutable.
4. Deletion occurs only after plan-v3 aggregate CI, independent disposition review and a machine-readable before/after ref manifest.
5. Closing a PR does not delete its branch. Source-freeze or other unique files must be explicitly accepted, reintroduced or rejected first.
6. No branch deletion may be used to hide failed checks, review history or evidence.

## 2. Keep

| Branch | Observed tip | Disposition | Reason |
| --- | --- | --- | --- |
| `main` | `326e670cb008a990247e31a63c0c4b0e338df62f` | keep/protect | Current default integration authority; protection remains an external-admin blocker. |
| `feat/plan-v3-gap-closure-2026-08-29` | dynamic; PR #42 | keep active | Current plan-v3 and blocker-closure line. |
| `archive/trillionnium-nakama-main-2026-08-28-7f0d4be` | `7f0d4be3e023aee86782bd3eb44a35f5dc991b15` | keep immutable | Preserves the pre-transition audited main. |
| `integration/all-branches-main-v1` | `3502f5b04cc06b974efc1582d232196c0bac3701` | retain pending disposition | PR #41 is closed as stale/superseded, but the branch contains five unique source-freeze files that must be explicitly accepted or rejected. |

## 3. Candidate duplicate WorldCommand refs

The following observed refs pointed to the same commit `278e2bfd27c2f90c54aa845b2501026a2b060168` at the audit observation. They are candidates for consolidation after proving that the commit/tree is reachable from the accepted main or an immutable archive and no active PR requires the named ref:

- `feature/game-world-command-deployed-fault-harness-v1`
- `feature/game-world-command-fault-delivery-v1`
- `feature/game-world-command-fault-evidence-v1-canonical`
- `feature/game-world-command-fault-evidence-v1-review`
- `feature/game-world-command-fault-evidence-v1`
- `feature/game-world-command-fault-harness-v1-final`
- `feature/game-world-command-fault-harness-v1-locked`
- `feature/game-world-command-fault-harness-v1-pr`
- `feature/game-world-command-fault-harness-v1-review`
- `feature/game-world-command-fault-harness-v1-source`
- `feature/game-world-command-fault-harness-v1`
- `feature/game-world-command-fault-matrix-v1`

Proposed result: preserve one semantically named immutable archive ref if the commit is not already covered by another approved archive; delete the remaining duplicate names only after the manifest and review pass.

## 4. Feature refs requiring ancestry and unique-tree review

| Branch | Observed tip | Proposed disposition |
| --- | --- | --- |
| `chore/nakama-runtime-bootstrap-v1` | `7f0d4be3e023aee86782bd3eb44a35f5dc991b15` | delete after confirming the immutable archive ref covers the identical commit and no PR depends on this name |
| `feature/nakama-authoritative-match-evidence-v1` | `b8d95fd7a29364a7014980e35dd9f4eb9b56b9fc` | compare/retain until evidence paths and ancestry are classified |
| `feature/nakama-authority-core-v1` | `b764004d9a283a8916e7758307d5027ffc321eee` | compare/retain until unique commits are accepted, superseded or archived |
| `feature/nakama-paper-raid-v1` | `d40d5cf97d7a345cc30f472274d01a1782549c58` | compare/retain until scope and unique data are classified |
| `feature/nakama-world-runtime-v1-consumer` | `c50a3e7ecb736bd375e77d69e12f83283a6d7054` | compare/retain until migration fixture ancestry is classified |
| `feature/trillionniumgame-v3-foundation` | `176116d3290d6e3ffff2818515bbb5b235e4f281` | compare against accepted foundation source and archive or delete after proof |
| `feature/trillionniumgame-w1-config-cli-readiness-v1` | `23e081e02e14f2a9b25a3f5448f69a3a6f7a5725` | retain until config/CLI source and evidence are integrated or rejected |
| `feature/trillionniumgame-w1-pgwire-persistence-adapter-v1` | `bcee51bc0c80ced445b19b0111cadf04f9540280` | retain until persistence adapter ancestry and database evidence are classified |

## 5. PR #41 source-freeze disposition

PR #41 was closed because its body claimed `78cb5e65…` while its actual head was `3502f5b0…`. Its five unique files are:

- four directory-level `rustfmt.toml` files disabling all formatting;
- `docs/development/SOURCE_FROZEN_RUST_ADAPTERS.md`.

Default plan-v3 position: security- and data-critical Rust is formatted, tested and strictly linted under the aggregate gate. A source-freeze exception may be reintroduced only as a focused PR with exact changed paths, expiry, independent security/data review and proof that it does not hide source defects. Until then, the unique files are preserved only on the retained integration branch and receive no mainline or compatibility credit.

## 6. Required machine manifest before deletion

The deletion operation must generate an immutable artifact with at least:

```json
{
  "repository_id": 1323087470,
  "observed_at": "RFC3339",
  "main_commit": "40-hex",
  "main_tree": "40-hex",
  "refs": [
    {
      "name": "refs/heads/...",
      "tip": "40-hex",
      "tree": "40-hex",
      "open_prs": [],
      "ancestor_of_main": true,
      "unique_commits": [],
      "disposition": "keep|archive|delete",
      "reviewer": "identity"
    }
  ]
}
```

After deletion, re-list all refs and verify that every `keep`/`archive` ref is unchanged, every approved `delete` ref is absent, all PR/evidence links remain readable and no source commit has become unreachable without an approved archive.

## 7. Closure boundary

`GAP-P1-BRANCH-001` remains open until the live branch inventory, unique-commit analysis, independent review, deletion operation and post-operation verification are all attached as accepted evidence. This document alone authorizes no deletion and closes no gate.
