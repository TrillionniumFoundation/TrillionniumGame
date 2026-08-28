# TrillionniumGame Project Boundary

- Project ID: `trillionnium-game`
- Repository: `TrillionniumFoundation/TrillionniumGame`
- Lane: `game-backend-platform`
- Lifecycle: `planning`
- Implementation boundary: first-party production service code is Rust
- Initial compatibility baseline: Nakama OSS `v3.40.0`

## Owns

- Full behavioral reimplementation of the Nakama OSS server baseline.
- HTTP/JSON, gRPC, WebSocket and Console protocol compatibility.
- Authentication, sessions, accounts, storage, social, chat, notifications, competitive systems, matchmaking, parties and multiplayer.
- Rust-native, WASM, Lua and JavaScript/TypeScript runtime hosting.
- Data migration and compatibility tooling for existing Nakama deployments.
- Console, observability, security, packaging, HA, backup, upgrade and cutover evidence.
- Ongoing upstream compatibility tracking after the first baseline.

## Does not own

- Heroic Cloud or non-public Nakama Enterprise implementations.
- External Satori/Hiro service implementations; only published integration surfaces.
- Official client SDK source repositories; only server-side compatibility tests.
- Trillionnium World gameplay rules, Chain consensus/finality, CEX ledger/custody, or cross-repository release orchestration.
- Binary loading of compiled Go runtime plugins in the final production product.

## Mandatory boundaries

- No first-party Go service or sidecar in the final production topology.
- No compatibility claim without exact differential evidence.
- No dual authority for one session, party, match, scheduler job, purchase or durable command.
- No external network work while a mutable database transaction is held.
- No silent scope reduction from the machine-readable parity denominator.
