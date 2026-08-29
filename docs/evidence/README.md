# Evidence index

## Current registry

- [`index.json`](index.json) — authoritative registry for evidence identity, target commit/tree, producer, artifact digests, schema status, review, expiry and claim credit.
- [`schemas/trillionnium-evidence-v1.schema.json`](schemas/trillionnium-evidence-v1.schema.json) — binding general evidence manifest schema.
- [`../development/EVIDENCE_MODEL.md`](../development/EVIDENCE_MODEL.md) — evidence principles, types, divergence and lifecycle policy.
- [`../testing/TEST_POLICY.md`](../testing/TEST_POLICY.md) — which execution classes and assertion metadata are required.

Evidence earns claim or gate credit only when all of the following are true:

1. the exact target repository, commit and tree match the candidate;
2. the artifact exists and its digest/size are verified;
3. the evidence schema is valid;
4. required execution is non-empty, terminal and successful;
5. limitations and divergences are explicit;
6. evidence is not expired or revoked;
7. an independent reviewer accepts it;
8. `scripts/derive-gates.py` associates it with the relevant gate.

CI logs, screenshots, issue comments, local output, workflow source and relay runs targeting another commit are diagnostics until indexed and validated. Empty, skipped or cancelled execution is never pass evidence.

## Existing legacy records

`2026-08-28-foundation-database-runtime-v2.json` is retained as a useful relay-produced database record, but the current index classifies it as legacy-schema/unreviewed and gives it no automatic compatibility or production credit. It must not be silently rewritten into accepted evidence; a current candidate requires a fresh exact-target manifest or an explicit reviewed conversion preserving provenance.

Historical WorldCommand evidence policy files remain scoped to their named tranche.
