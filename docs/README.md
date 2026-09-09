# TrillionniumGame documentation

Status: **authoritative current documentation**  
Revision: 2026-09-03

This is the only human documentation index for the active repository tree. Earlier design notes, dated progress snapshots, alpha documents, version-suffixed drafts and duplicate topic READMEs have been removed from the active tree. Their history remains available through Git, pull requests and issues; immutable machine evidence remains under `docs/evidence/`.

## Read in this order

1. [`../CURRENT_PLAN.md`](../CURRENT_PLAN.md) — complete scope, workstreams, stage gates and closure rules.
2. [`ARCHITECTURE.md`](ARCHITECTURE.md) — current and target runtime, component boundaries and data flow.
3. [`DEVELOPMENT.md`](DEVELOPMENT.md) — repository layout, toolchains, local workflow and coding constraints.
4. [`COMPATIBILITY.md`](COMPATIBILITY.md) — pinned Nakama baseline, denominator, oracle and compatibility claims.
5. [`TESTING_AND_EVIDENCE.md`](TESTING_AND_EVIDENCE.md) — test classes, CI, artifact identity and independent review.
6. [`SECURITY_AND_PRIVACY.md`](SECURITY_AND_PRIVACY.md) — cryptography, keys, sessions, secrets, privacy and supply chain.
7. [`OPERATIONS_AND_RELEASE.md`](OPERATIONS_AND_RELEASE.md) — configuration, lifecycle, databases, observability, release and retirement.
8. [`GOVERNANCE.md`](GOVERNANCE.md) — branch policy, CODEOWNERS, required reviews and administrative acceptance.
9. [`ROADMAP.md`](ROADMAP.md) — current critical path, sequencing and parallel-development boundary.

## Machine authority

Human summaries never override machine state. Current execution and claims are derived from:

- [`status/CURRENT_STATE.json`](status/CURRENT_STATE.json)
- [`status/EXECUTION_STATUS.json`](status/EXECUTION_STATUS.json)
- [`status/GAP_REGISTER.json`](status/GAP_REGISTER.json)
- [`status/IMPLEMENTATION_INVENTORY.json`](status/IMPLEMENTATION_INVENTORY.json)
- [`status/PRODUCT_GATES.json`](status/PRODUCT_GATES.json)
- [`status/RISK_REGISTER.json`](status/RISK_REGISTER.json)
- [`roadmap/NEXT_MILESTONE.json`](roadmap/NEXT_MILESTONE.json)
- [`evidence/index.json`](evidence/index.json)
- [`development/PARITY_DENOMINATORS.json`](development/PARITY_DENOMINATORS.json)
- [`development/FEATURE_PARITY_MATRIX.md`](development/FEATURE_PARITY_MATRIX.md)
- [`DOCUMENTATION_AUTHORITY.json`](DOCUMENTATION_AUTHORITY.json)

The dated JSON files under evidence or status directories are records, not alternate current plans. An evidence item counts only when the evidence and gap validators accept its exact target identity and required independent review.

## Documentation change rule

A development change must update the smallest applicable current topic document and any affected machine state in the same pull request. Do not add a second document for the same topic. Do not create `V2`, `FINAL`, `NEW`, `ALPHA`, `CANDIDATE` or date-stamped Markdown files. Replace the current topic document and rely on Git history for prior versions.

The authority's `revision` is the common baseline for current topic documents. Its optional `document_revisions` object binds later revisions to exact paths already registered in `current_human_documents`. An omitted override uses the exact common baseline; arbitrary dates do not pass. Updating one topic requires updating its marker and its registry entry together, not rewriting unrelated topics or backdating the changed document.

Each current topic has exactly one whole-line `Revision:` marker matching its effective registered date. Dates must be canonical real calendar dates, and an override cannot predate the common baseline. Trailing Markdown whitespace and CRLF are accepted; duplicate markers, substring matches, undeclared paths, malformed maps/dates and duplicate JSON keys are rejected. Validation does not consult the current clock, so the same checkout is reproducible. The change does not expand the nine-topic allowlist or weaken module, link, reference, history or claim checks.

Run:

```bash
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m unittest discover -s tests/control_plane -p 'test_document_revision_binding.py' -v
```

The documentation authority check rejects undeclared Markdown under `docs/`, removed legacy directories, broken local links, stale repository-path references and reintroduced legacy naming patterns. Revision regression tests run the real validator against temporary repository fixtures, including negative allowlist, missing-module, broken-link and premature-production-claim cases. These fixture results do not substitute for checking the complete exact source and prospective-merge trees in CI.

## Claim boundary

The consolidated documentation describes the intended system and the current source frontier. It does not assert complete Nakama compatibility, C1–C5, SG1–SG9, production readiness, public-online approval, drop-in replacement or Nakama retirement.
