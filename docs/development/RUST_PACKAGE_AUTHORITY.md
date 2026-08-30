# Rust package and binary authority

Status: binding plan-v3 source-control contract. It does not grant build, compatibility or production credit.

Machine authority: [`RUST_PACKAGE_AUTHORITY.json`](RUST_PACKAGE_AUTHORITY.json).  
Checker: [`../../scripts/check-rust-package-inventory.py`](../../scripts/check-rust-package-inventory.py).

## Why this contract exists

A Rust package can be present in the repository without being compiled by the root workspace. An explicit target can also point to a file that does not exist. Both failures are dangerous in this program because source presence may look like progress while the actual merge gate never builds the package.

The repository previously contained a second `crates/trnm-server` package whose manifest declared a missing `src/lib.rs`. It was outside the root workspace and competed with the working vertical-slice binary in `trnm-persistence-pg`. That duplicate package has been removed rather than retained as an unverified second authority.

## Current authority

The current first-party server binary target is:

```text
manifest: crates/trnm-persistence-pg/Cargo.toml
binary:   trnm-server
source:   crates/trnm-persistence-pg/src/bin/trnm-server.rs
```

This placement is temporary. It keeps the narrow server slice next to the exact PG-wire adapter and authoritative migration ABI while the composition boundary is still being proven. It does not make persistence and process supervision the same permanent component.

A future extraction to `crates/trnm-server` must be one atomic reviewed change. The replacement package, source, tests, status inventory, lockfile and merge-gate coverage must appear together; the old target must disappear in the same accepted change. Two first-party `trnm-server` targets may never coexist.

## Root workspace

Every ordinary first-party Rust package is a member of the root workspace and therefore participates in:

```text
cargo fmt --all -- --check
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
```

The workspace list in `Cargo.toml` and the list in `RUST_PACKAGE_AUTHORITY.json` must be byte-for-byte equivalent as normalized repository paths.

## Isolated workspaces

A package may be isolated only when its manifest is explicitly listed in the machine authority. Isolation is not a waiver. Every isolated manifest must have an immutable, mandatory matrix entry in `trillionnium-game-merge-gate` that executes format, all-target tests and strict Clippy.

The current isolated set consists of the JWT compatibility adapter, its two source gates, and the presence-router-v2 integration candidate. Adding another isolated workspace requires an ADR or reviewed update explaining why it cannot be a root member and how it remains impossible to omit from merge validation.

## Discovery and target rules

The checker fails when any of the following is true:

- a `crates/**/Cargo.toml` is neither a root member nor an approved isolated workspace;
- a declared root member or isolated manifest is missing;
- a root member is also excluded;
- an explicit `[lib]` or `[[bin]]` path does not exist;
- a package has no discoverable library or binary target;
- an isolated workspace is absent from the aggregate gate;
- an unapproved duplicate package name appears;
- zero or more than one first-party binary is named `trnm-server`;
- the authoritative server manifest, source path or binary name drifts from the machine contract.

Auto-discovered `src/lib.rs`, `src/main.rs`, `src/bin/*.rs` and `src/bin/*/main.rs` targets are included in the inventory. The checker emits a deterministic JSON report containing manifests, package names and target paths.

## Claim boundary

Passing the package inventory checker proves only that every declared Rust package and target is structurally accounted for. It does not prove that Cargo compiled successfully, that live databases ran, that protocol behavior matches Nakama, or that any product gate is closed. Those conclusions still require non-empty exact-head execution, indexed artifacts and the required independent review.
