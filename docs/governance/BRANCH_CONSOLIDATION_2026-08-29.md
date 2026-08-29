# Repository branch consolidation — 2026-08-29

This record freezes the exact remote branch state immediately before the repository is reduced to one `main` branch.

## Policy

The validated `integration/all-branches-main-v1` tree is the content authority. Historical and experimental tips are merged as Git ancestry, but their obsolete file trees are **not** mechanically reintroduced. This is equivalent to an audited `ours` consolidation: history is retained, current product content remains authoritative.

The final consolidation commit must contain the pre-consolidation `main`, the integration head, and every tip that was not already reachable from the integration head as parents. Before ref deletion, every branch tip in the JSON inventory must be provably reachable from the new `main`.

## Frozen state

- Repository ID: `1323087470`
- Branches before cleanup: `47`
- Non-`main` branches before cleanup: `46`
- Unique tips: `33`
- Main before cleanup: `41536ea2bee578c33b15ec00b3f2cef4ea4309ce`
- Integration content head before audit updates: `3884f68e98137bdb0546bca29a5e8cece374a2e8`
- Integration content tree before audit updates: `f9b7760bf0b6225fcb75a1644fc206a96fd8d0e4`
- Inventory SHA-256: `72764598fcbdfc6d61473f8d587d2609bc334fe9cfcd3286aa80d27b0fccd967`

## Non-ancestor tips explicitly absorbed

- `44ad02d52ff0dd5b43f5653f8875fb376710b6d9` — deployed World runtime branch advanced after the first ancestry audit.
- `d40d5cf97d7a345cc30f472274d01a1782549c58` — historical paper-raid branch.
- `c50a3e7ecb736bd375e77d69e12f83283a6d7054` — historical World runtime consumer branch.

## Required final state

1. Every recorded tip is an ancestor of `main`.
2. The GitHub heads collection contains exactly `refs/heads/main`.
3. The one-time consolidation workflow is absent from the final content tree.
4. The repository remains fail-closed with respect to compatibility and production claims; branch cleanup does not itself earn Nakama parity or release readiness.
