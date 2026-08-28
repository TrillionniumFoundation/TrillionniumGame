# TrillionniumGame Project Boundary

- Project ID: `trillionnium-game`
- Current GitHub repository: `TrillionniumFoundation/Trillionnium-Nakama`
- Target GitHub repository name: `TrillionniumFoundation/TrillionniumGame`
- Repository ID to preserve: `1323087470`
- Lane: `game-backend-platform`
- Lifecycle: `planning-audited-v2`
- First-party production service language: Rust
- Initial compatibility baseline: Nakama OSS `v3.40.0`

## Owns

- Full behavioral reimplementation of the public Nakama OSS server baseline.
- HTTP/JSON, gRPC, WebSocket and Console protocol compatibility.
- Authentication, sessions, accounts, storage, social, chat, notifications, competition, matchmaking, parties and multiplayer.
- Rust-native, WASM, Lua and JavaScript/TypeScript runtime hosting.
- Source migration tooling for existing Go runtime modules.
- Nakama schema/data migration, Console, observability, security, packaging, HA, backup, upgrade and cutover evidence.
- Ongoing upstream-delta analysis after the initial baseline.

## Does not own

- Heroic Cloud or non-public Enterprise implementations.
- External Satori/Hiro products; only published integration surfaces.
- Official client SDK source repositories; only server compatibility with declared SDK versions.
- Trillionnium World gameplay rules, Chain consensus/finality, CEX ledger/custody, or cross-repository release orchestration.
- Binary ABI support for already compiled Go plugins.

## Mandatory boundaries

- No first-party Go server, Go sidecar or compiled-Go-plugin loader in the final production topology.
- No broad compatibility claim beyond the verified C0–C5 profile.
- No compatibility claim without exact upstream identity, oracle lock and immutable evidence.
- No dual authority for one session, party, ticket, match, scheduler job, purchase or durable command.
- No external network work while a mutable database transaction is held.
- No silent scope reduction from generated D0–D8 leaf denominators.
- Repository rename must preserve repository ID, history, refs, issues and pull requests; delete/recreate is forbidden.
