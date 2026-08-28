# Contributing

## Development contract

All changes must preserve the project boundary and the full-parity program.

A pull request must include:

- a single accountable scope;
- tests appropriate to protocol, data, concurrency, failure and security impact;
- upstream oracle fixtures for compatibility work;
- documentation and machine-readable status updates;
- explicit residual limitations;
- no unrelated feature expansion.

## Required local check

```bash
python3 scripts/check-plan.py
```

Rust implementation pull requests will additionally run formatting, Clippy, unit/property/fuzz, SQL migration, differential, dependency, license and security gates as those capabilities land.

## Commit style

Use focused conventional-style subjects, for example:

```text
feat(auth): add device authentication differential slice
fix(storage): fence stale object-version retries
contracts(rtapi): lock websocket error vectors
```

## Compatibility evidence

A behavioral parity claim must identify:

- upstream repository, tag, commit, tree and source blob;
- TrillionniumGame commit and artifact digest;
- exact test command and environment;
- expected and observed outputs;
- normalization rules;
- limitations and reviewer.
