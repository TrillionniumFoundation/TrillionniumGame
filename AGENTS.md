# Project Boundary (binding)

This Git root is **Trillionnium Nakama** (`trillionnium-nakama`), lane
`nakama-realtime`. Run `bash scripts/project-preflight.sh` before any write,
build, commit, branch, remote, or dependency change.

This repository owns matchmaking, rooms, presence, authoritative match state,
and replay-event-root production. It does not own Hepta evaluation/settlement,
Chain finality/runtime, World campaign/gameplay rules, CEX ledger state, or the
cross-repository E2E harness. Exchange behavior through versioned contracts;
never depend on a sibling working tree.
