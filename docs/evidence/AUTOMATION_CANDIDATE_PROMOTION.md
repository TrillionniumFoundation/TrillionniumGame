# Automation evidence candidate and review promotion

Status: binding plan-v3 evidence workflow.

## Problem

A successful workflow is necessary but not sufficient evidence. It may target the wrong SHA, have zero assertions, upload no artifact, omit limitations, be produced by a relay against stale source, or be interpreted by its author without independent review.

## Stage 1 — automation candidate

After a successful exact-head job, `scripts/build-automation-evidence-candidate.py` creates an envelope conforming to:

```text
docs/evidence/schemas/automation-evidence-candidate-v1.schema.json
```

The builder requires:

- candidate identity manifest whose commit equals `GITHUB_SHA`;
- exact workflow/run/attempt/job identity;
- at least one non-zero true assertion in a structured result;
- non-empty artifacts with SHA-256 and sizes;
- exact commands;
- explicit limitations;
- required independent review roles.

The generated claims are always:

```text
automation_passed = true
accepted = false
gap_closed = false
compatibility_credit = false
production_ready = false
```

Automation cannot change those values.

## Stage 2 — independent review

A reviewer who did not implement the candidate examines:

- exact source commit/tree and candidate manifest;
- workflow/job logs and assertion accounting;
- environment/profile and fixtures;
- artifact digests and reproducibility;
- fault/negative coverage;
- divergences, limitations and expiry;
- claim/gap scope.

The reviewer creates a normal `trillionnium.evidence.v1` record with an accepted/rejected/needs-work decision. Promotion is rejected if the evidence references an older candidate, missing artifact, stale review or self-approval.

## Stage 3 — index and derivation

Only accepted `trillionnium.evidence.v1` is entered as valid evidence in `docs/evidence/index.json`. Gap and gate derivation then evaluates dependencies, child divergences, expiry and exact candidate identity.

Merging a PR or closing an issue does not replace these stages.

## Relay evidence

A relay job follows the same process and additionally must verify the target repository's candidate identity artifact. A successful relay against a different target commit/tree or migration-chain digest is diagnostic only.

## Security

The builder hashes files and records only a selected non-secret environment summary. Database URLs, tokens, private keys and provider receipts must never be copied into the evidence envelope or logs.
