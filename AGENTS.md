# Agent Instructions

1. Read `CURRENT_PLAN.md`, `PROJECT_BOUNDARY.md`, `docs/status/CURRENT_STATE.json`, `docs/status/GAP_REGISTER.json`, `docs/roadmap/NEXT_MILESTONE.json`, the evidence index, schema authority, compatibility profiles and migration authority matrix before changing code.
2. Work from the exact live branch/PR head. Revalidate repository, commit and tree after every source push; copied stale SHA text is not authority.
3. Do not reduce the Nakama parity denominator because a feature is unused by one Trillionnium product. `FEATURE_PARITY_MATRIX.md` is a human roll-up; generated leaf manifests are the machine source of truth.
4. First-party target production service code must be Rust. The current Go plugin is migration input/oracle only; do not add Go services, Go sidecars or a compiled Go plugin loader.
5. Never use `drop-in`, `compatible`, `production-ready` or `replacement` beyond an evidence-derived C0-C5 capability/profile claim.
6. Source presence, local tests, workflow YAML, issue closure, relay evidence for an older target, empty checks and self-review do not close a gap or gate.
7. Every P0/P1 gap closes only with exact candidate execution, indexed artifacts, closed dependencies, zero blocking unexplained divergence and accepted independent review.
8. Preserve `migrations/postgresql` and `migrations/cockroachdb` as the only production-authoritative schema chains. `database/schema/v2` is design history and must not be consumed by runtime, live CI, backup, restore or release tooling.
9. Changes to the first Rust process must preserve `docs/development/TRNM_SERVER_VERTICAL_SLICE.md`, `docs/status/TRNM_SERVER_STATUS.json` and `scripts/check-trnm-server.py` together.
10. Until real session middleware replaces it, every bootstrap/commit/drain mutation must authenticate before parsing business input or calling a repository.
11. Construct external HTTP/WebSocket acknowledgement only after durable commit or exact receipt replay. A response-delivery failure must not compensate, double-apply or perform external I/O in the transaction.
12. Use bounded queues, frame/request sizes, deadlines, attempts, memory and ownership generations for every asynchronous or network boundary. Missing limits are blockers.
13. Keep external I/O outside mutable database transactions; external effects use durable intent/outbox/reconciliation.
14. Do not claim Nakama compatibility without exact immutable/instrumented oracle evidence and an independently reviewed normalizer registry. Identity, ACL, order, amount, version, cursor, error code and durable-effect differences cannot be normalized.
15. Every protocol or data change requires schemas, fixtures, negative tests, migration impact, rollback/forward-fix and evidence paths.
16. Security-critical crates must run in the aggregate gate even when they remain standalone workspaces. Hand-written cryptographic or handshake primitives require published vectors, fuzzing and independent review.
17. Do not import sibling repository source paths. Consume versioned crates, contracts or artifacts and validate exact target identity before accepting relay evidence.
18. Preserve Apache-2.0 attribution for upstream-derived material and update `NOTICE`/source manifests when required.
19. Update task, gap, divergence, risk, component status, evidence and gate state together. A status-only edit cannot grant completion.
20. Run `make check` locally when tools are available. Local results are diagnostic only; required target-native checks, live database/oracle lanes and independent review remain mandatory.
21. Keep pull requests Draft while exact-head checks are absent, skipped, cancelled, stale or failed. Never merge your own unreviewed evidence line.
22. Do not create dual authority for sessions, parties, tickets, matches, schedulers, purchases, outbox effects or durable commands.
