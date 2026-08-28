# Agent Instructions

1. Read `CURRENT_PLAN.md`, `PROJECT_BOUNDARY.md`, and ADR-0001 before changing code.
2. Do not reduce the parity denominator because a feature is not currently used by one Trillionnium product.
3. First-party production service code must be Rust. Do not add Go services, Go sidecars, or a compiled Go plugin loader.
4. Do not claim Nakama compatibility without an exact upstream baseline and differential evidence.
5. Every protocol or data change requires schema, fixtures, negative tests, migration impact and rollback notes.
6. Keep external I/O outside mutable database transactions.
7. Use bounded queues, explicit deadlines and cancellation for every asynchronous boundary.
8. Do not import sibling repository source paths. Consume versioned crates/contracts/artifacts.
9. Preserve Apache-2.0 attribution for upstream-derived material and update `NOTICE` when required.
10. Update the machine-readable backlog and product gates whenever status changes.
