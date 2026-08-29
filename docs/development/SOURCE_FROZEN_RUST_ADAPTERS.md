# Source-frozen Rust compatibility adapters

Status: narrowly scoped integration policy; not compatibility or production credit.

## Scope

Only the following historical compatibility adapters opt out of rustfmt rewriting:

- `crates/trnm-persistence-pg`
- `crates/trnm-token-jwt-adapter`
- `crates/trnm-token-jwt-adapter-gate`
- `crates/trnm-token-jwt-adapter-gate-v2`

Each directory contains `rustfmt.toml` with `disable_all_formatting = true`. The root Rust workspace and every other Rust source remain under Rust 1.85.1 `cargo fmt --check`.

## Why this exists

These adapters arrived through independently tested compatibility branches. Reformatting them produces large mechanical diffs unrelated to behavior and previously made exact artifact transfer error-prone. The exception preserves reviewed bytes while the branch histories are consolidated.

This is not a waiver for correctness. Every source-frozen adapter must continue to pass:

- all-target unit and integration tests;
- strict Clippy with warnings denied;
- source and contract hash checks;
- the PostgreSQL and CockroachDB live lanes where applicable;
- security and negative-vector suites.

## Change control

Any semantic edit to a source-frozen Rust file must either:

1. remove the directory-level freeze and commit the complete Rust 1.85.1 formatting result; or
2. update this record with the exact changed paths, independent review evidence, and a replacement expiry decision.

The policy expires at the earlier of SG4 foundation integration approval or 2026-10-31. It cannot close SG1-SG9, earn C1-C5, or support a production-readiness claim by itself.
