# Agent Instructions

1. Read `CURRENT_PLAN.md`, `PROJECT_BOUNDARY.md`, ADR-0001, the plan audit, compatibility profiles, denominator spec and migration authority matrix before changing code.
2. Do not reduce the parity denominator because a feature is not currently used by one Trillionnium product.
3. `FEATURE_PARITY_MATRIX.md` is a human roll-up; generated leaf manifests are the machine source of truth.
4. First-party production service code must be Rust. Do not add Go services, Go sidecars, or a compiled Go plugin loader.
5. Never use `drop-in`, `compatible`, `production-ready` or `replacement` beyond the verified C0–C5 profile.
6. Do not claim Nakama compatibility without exact immutable/instrumented oracle evidence and a reviewed normalizer registry.
7. Every protocol or data change requires schema, fixtures, negative tests, migration impact, rollback and evidence paths.
8. Keep external I/O outside mutable database transactions; external effects use durable intent/outbox/reconciliation.
9. Use bounded queues, explicit deadlines, cancellation and ownership generations for every asynchronous boundary.
10. Do not import sibling repository source paths. Consume versioned crates/contracts/artifacts.
11. Preserve Apache-2.0 attribution for upstream-derived material and update `NOTICE`/source manifests when required.
12. Update task, parity, risk, gate and evidence state together; a status-only edit cannot close work.
13. Do not normalize identity, ACL, sequence, money, version, cursor, error code or durable-effect divergence.
14. Do not create dual authority for sessions, parties, tickets, matches, schedulers, purchases or durable commands.
