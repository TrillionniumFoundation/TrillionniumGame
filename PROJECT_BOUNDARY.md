# TrillionniumGame Project Boundary

- Project ID: `trillionnium-game`
- Current GitHub repository: `TrillionniumFoundation/TrillionniumGame`
- Previous GitHub repository name: `TrillionniumFoundation/Trillionnium-Nakama`
- Repository ID preserved across rename: `1323087470`
- Lane: `game-backend-platform`
- Lifecycle: `plan-v3.1-current`
- First-party production service language: Rust
- Initial compatibility baseline: Nakama OSS `v3.40.0`
- Current execution plan: [`CURRENT_PLAN.md`](CURRENT_PLAN.md)
- Current documentation index: [`docs/README.md`](docs/README.md)

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
- Trillionnium World gameplay rules, Chain consensus/finality, CEX ledger/custody or cross-repository release orchestration.
- Binary ABI support for already compiled Go plugins.

## Mandatory boundaries

- No first-party Go server, Go sidecar or compiled-Go-plugin loader in the final production topology.
- No broad compatibility claim beyond the verified C0–C5 capability/profile.
- No compatibility claim without exact upstream identity, oracle lock and immutable evidence.
- No dual authority for one session, party, ticket, match, scheduler job, purchase or durable command.
- No external network work while a mutable database transaction is held.
- No silent scope reduction from generated D0–D8 leaf denominators.
- The completed repository rename must continue to preserve repository ID, history, refs, issues and pull requests; delete/recreate remains forbidden.
- Human development authority is limited to the documents declared by `docs/DOCUMENTATION_AUTHORITY.json`; prior versions remain in Git history rather than the active tree.

## Current claim boundary

```text
complete Nakama compatibility = false
C1-C5 repository-wide = false
SG1-SG9 = false
production-ready = false
public-online = false
drop-in replacement = false
Nakama retired = false
```
