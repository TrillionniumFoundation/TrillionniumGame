# Contributing

## Development contract

Every change must preserve the full Nakama OSS parity scope, single-authority rules and fail-closed claim boundary in `CURRENT_PLAN.md`.

A pull request must include:

- one accountable scope and exact base/head identity;
- the affected gap, task, parity leaf and product-gate mappings;
- tests appropriate to protocol, data, concurrency, failure, restart and security impact;
- immutable upstream/oracle fixtures for compatibility work;
- documentation, machine-readable status and evidence-index updates;
- explicit residual limitations and rollback/forward-fix impact;
- no unrelated feature expansion;
- no compatibility or production promotion from source presence alone.

## Required local aggregate check

Use the same logical lanes as `.github/workflows/trillionnium-game-merge-gate.yml`:

```bash
make check
```

This runs:

```text
plan-v3 scope/status/evidence/schema contracts
trnm-server source contract
root Rust format, all-target tests and strict Clippy
standalone security-critical Rust workspaces
Python contracts and denominator tests
legacy Go migration-input format, test, race and vet
```

Live PostgreSQL/CockroachDB, immutable oracle, SDK, fault, HA, load, restore and security lanes are separate evidence obligations. A developer-only database skip or local result cannot produce remote verification credit.

## Rust server changes

Changes under `crates/trnm-persistence-pg/src/bin/trnm_server/` must preserve:

- explicit `check-config`, `migrate` or `serve`; no silent serve default;
- loopback and plaintext-database fail-closed defaults;
- the unique production schema authority under `migrations/`;
- bounded request/frame sizes, timeouts and retry attempts;
- authentication before all mutations until session middleware replaces the candidate credential;
- response construction only after commit or exact duplicate receipt replay;
- public/internal error separation;
- false compatibility, SG4 and production claims until accepted evidence exists.

## Commit style

Use focused conventional-style subjects, for example:

```text
feat(auth): add device authentication differential slice
fix(storage): derive public content version from exact value bytes
contracts(rtapi): lock websocket error vectors
```

## Compatibility evidence

A parity claim must identify:

- upstream repository, tag, commit, tree and source blob;
- TrillionniumGame commit, tree and artifact digest;
- exact test command and environment;
- expected and observed outputs;
- assertion totals and divergences;
- normalization rules;
- limitations, expiry and independent reviewer.

Empty, skipped, cancelled, absent or older-head checks are failure to prove, not pass. Implementers may not self-approve their own P0/P1 evidence.
