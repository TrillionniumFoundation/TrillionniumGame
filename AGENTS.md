# Agent Instructions

1. Start with `CURRENT_PLAN.md` and `docs/README.md`. Read the applicable current topic documents plus `docs/status/CURRENT_STATE.json`, `docs/status/GAP_REGISTER.json`, `docs/roadmap/NEXT_MILESTONE.json` and `docs/evidence/index.json` before changing code.
2. `docs/DOCUMENTATION_AUTHORITY.json` defines the live human documentation set. Update the existing topic document; never add date-stamped, versioned, `ALPHA`, `CANDIDATE`, `FINAL` or duplicate topic Markdown. Git history is the human-document archive.
3. Work from the exact live branch and pull-request head. Revalidate repository, commit and tree after every source push; copied SHA text is not authority.
4. Do not reduce the Nakama parity denominator because a feature is unused by one Trillionnium product. `docs/development/FEATURE_PARITY_MATRIX.md` is a generated human roll-up; generated leaf manifests are the machine source of truth.
5. First-party target production service code must be Rust. The current Go plugin is migration input/oracle only; do not add Go services, Go sidecars or a compiled Go plugin loader.
6. Never use `drop-in`, `compatible`, `production-ready` or `replacement` beyond an evidence-derived C0–C5 capability/profile claim.
7. Source presence, local tests, workflow YAML, issue closure, relay evidence for an older target, empty checks and self-review do not close a gap or gate.
8. Every P0/P1 gap closes only with exact candidate execution, indexed artifacts, closed dependencies, zero blocking unexplained divergence and accepted independent review.
9. Preserve `migrations/postgresql` and `migrations/cockroachdb` as the only production-authoritative schema chains. `database/schema/v2` is design history and must not be consumed by runtime, live CI, backup, restore or release tooling.
10. Changes to the canonical database-backed Rust process must keep `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT.md`, `docs/status/TRNM_SERVER_STATUS.json` and `scripts/check-trnm-server.py` consistent. Changes to the standalone foundation process must keep `contracts/server/vertical-slice-v1.json` and `scripts/check-rust-server-source-candidate.py` consistent.
11. Until production session middleware and administration identity fully replace candidate credentials, every bootstrap, commit, drain and privileged mutation authenticates before business parsing or repository access.
12. Construct external HTTP/WebSocket acknowledgement only after durable commit or exact receipt replay. Response-delivery failure must not compensate, double-apply or perform external I/O in the transaction.
13. Use bounded queues, frame/request sizes, deadlines, attempts, memory and ownership generations for every asynchronous or network boundary. Missing limits are blockers.
14. Keep external I/O outside mutable database transactions; external effects use durable intent, outbox and reconciliation.
15. Do not claim Nakama compatibility without exact immutable/instrumented oracle evidence and an independently reviewed normalizer registry. Identity, ACL, order, amount, version, cursor, error code and durable-effect differences cannot be normalized.
16. Every protocol or data change requires schemas, fixtures, negative tests, migration impact, rollback/forward-fix and evidence paths.
17. Security-critical crates run in the aggregate gate even when they remain standalone workspaces. Hand-written cryptographic or handshake primitives require standards vectors, fuzzing and independent review.
18. Do not import sibling repository source paths. Consume versioned crates, contracts or artifacts and validate exact target identity before accepting relay evidence.
19. Preserve Apache-2.0 attribution for upstream-derived material and update `NOTICE` and source manifests when required.
20. Update task, gap, divergence, risk, component status, evidence and gate state together. A status-only edit cannot grant completion.
21. Run `python3 scripts/check-documentation-authority.py` and `make check` locally when tools are available. Local results are diagnostic; target-native live database/oracle lanes and independent review remain mandatory.
22. Keep pull requests Draft while exact-head checks are absent, skipped, cancelled, stale or failed. Never merge your own unreviewed evidence line.
23. Do not create dual authority for sessions, parties, tickets, matches, schedulers, purchases, outbox effects or durable commands.
