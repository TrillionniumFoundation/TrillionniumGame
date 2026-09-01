# Contributing

## Development contract

Every change preserves the full Nakama OSS parity scope, single-authority rules and fail-closed claim boundary in [`CURRENT_PLAN.md`](CURRENT_PLAN.md). The current documentation index is [`docs/README.md`](docs/README.md); update the applicable existing topic document instead of creating another version or progress snapshot.

A pull request includes:

- one accountable scope and exact base/head identity;
- affected gap, task, parity leaf and product/stage-gate mappings;
- tests appropriate to protocol, data, concurrency, failure, restart and security impact;
- immutable upstream/oracle fixtures for compatibility work;
- current documentation, machine-readable status and evidence-index updates;
- explicit residual limitations and rollback/forward-fix impact;
- no unrelated feature expansion;
- no compatibility or production promotion from source presence alone.

## Required local aggregate check

Run documentation/control authority first:

```bash
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
```

Then use the same logical lanes as `.github/workflows/trillionnium-game-merge-gate.yml`:

```bash
make check
```

This covers plan/status/evidence/schema contracts, the current documentation allowlist, server source contracts, root Rust format/all-target tests/strict Clippy, isolated security-critical Rust workspaces, complete Python discovery and the Go migration-input format/test/race/vet suite.

Live PostgreSQL/CockroachDB, immutable oracle, SDK, fault, HA, load, restore and security lanes are separate evidence obligations. A developer-only database skip or local result cannot produce remote verification credit.

## Documentation changes

`docs/DOCUMENTATION_AUTHORITY.json` is the allowlist. Under `docs/`, only the nine current topic documents and generated `docs/development/FEATURE_PARITY_MATRIX.md` are human-readable Markdown. Machine status/evidence JSON remains in its established paths.

Do not add `V2`, `FINAL`, `NEW`, `ALPHA`, `CANDIDATE`, `SUPERSEDED`, date-stamped Markdown or a second README for a topic. Replace the current document and use Git history for prior content. Do not retain redirect stubs; migrate active references and remove the obsolete file.

## Rust server changes

Changes under `crates/trnm-persistence-pg/src/bin/trnm_server/` preserve:

- explicit `check-config`, `migrate` or `serve`; no silent serve default;
- loopback and plaintext-database fail-closed defaults;
- the unique production schema authority under `migrations/`;
- bounded request/frame sizes, timeouts and retry attempts;
- authentication before all mutations until production session/administration middleware replaces candidate credentials;
- response construction only after commit or exact duplicate receipt replay;
- public/internal error separation;
- false compatibility, SG4 and production claims until accepted evidence exists.

Changes under `crates/trnm-server/` preserve its explicitly limited foundation contract in `contracts/server/vertical-slice-v1.json`; that package is not a second production composition root.

## Commit style

Use focused conventional subjects, for example:

```text
feat(auth): add device authentication differential slice
fix(storage): derive public content version from exact value bytes
docs(control): consolidate current architecture authority
```

## Compatibility evidence

A parity claim identifies:

- upstream repository, tag, commit, tree and source blob;
- TrillionniumGame commit, tree and artifact digest;
- exact test command and environment;
- expected and observed outputs;
- assertion totals and divergences;
- normalization rules;
- limitations, expiry and independent reviewer.

Empty, skipped, cancelled, absent, zero-job or older-head checks are failure to prove, not pass. Implementers may not self-approve their own P0/P1 evidence.

## Review and merge

Keep the pull request Draft while required checks, exact identity or P0/P1 findings are unresolved. The final head requires the applicable independent CODEOWNER/database/security/protocol/realtime/SRE review. Administrator or self-merge bypass cannot grant compatibility, production, public-online, replacement or retirement authority.
