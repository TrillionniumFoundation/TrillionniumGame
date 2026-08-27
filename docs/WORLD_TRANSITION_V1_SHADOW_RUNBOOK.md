---
status: current
owner: trillionnium-nakama
contract_version: trnm_nakama_world_transition_shadow_runbook_v1
world_contract: trnm_world_transition_v1
applies_to:
  - WORLD-P0-003
  - shadow-only
last_reviewed: 2026-08-28
review_due: 2026-09-11
---

# World transition v1 shadow runbook

## Objective

Compare a World observation and a Nakama candidate observation for the same
already-authoritative input without giving either shadow path permission to
publish, settle, sign completion or take over a live match.

## Preconditions

1. World contract commit/tree and vendored blobs match
   `contracts/world-transition-v1-consumer-lock.json`.
2. Nakama authority context was admitted and sequenced before World execution.
3. Both observations bind an exact implementation commit.
4. The same request hash and authority-context fingerprint are used.
5. Neither path can produce an externally visible completion or settlement.
6. The compatibility World path remains laboratory-only.

## Observation fields

Each `trnm_nakama_world_transition_observation_v1` record includes:

- fixture ID;
- implementation ID and exact 40-hex revision;
- Nakama authority-context fingerprint;
- World request hash;
- accepted/rejected disposition;
- deterministic tick and material hashes, or stable rejection fields;
- SHA-256 of the complete canonical World result;
- diagnostic execution duration.

Duration is recorded but not treated as deterministic equality. Capacity
promotion uses a separate reviewed matrix.

## Comparison

Run:

```bash
PYTHONPATH=. python3 -m runtime.world_transition_v1 compare-shadow \
  --world /evidence/world-observations.jsonl \
  --candidate /evidence/nakama-observations.jsonl \
  --summary /evidence/shadow-summary.json
```

The command exits non-zero for:

- missing, unexpected or duplicate fixtures;
- authority-context mismatch;
- request mismatch;
- accepted/rejected disposition mismatch;
- tick, state, replay, outcome or transition hash mismatch;
- rejection code or retryability mismatch;
- different canonical result bytes;
- malformed observation records.

A matched summary sets only:

```json
"promotion_eligible_for_integration_review": true
```

It always keeps:

```json
"cutover_authorized": false,
"canonical_completion_signing_performed": false,
"public_online_enabled": false
```

## Required corpora

Before Integration may review cutover readiness, run at least:

- all canonical and negative World vectors;
- supported ruleset/content revision pairs;
- accepted commands at boundary ticks;
- every stable rejection code;
- repeated idempotent command delivery;
- process restart before and after World response persistence;
- malformed/tampered World responses;
- payload and nesting resource boundaries;
- deterministic replay/outcome terminal cases;
- representative concurrency and duration matrix.

Any unexplained divergence blocks promotion. Do not average, suppress or mark a
partial corpus green.

## Synthetic contract fixtures

`tools/emit_world_transition_v1_shadow_fixture.py` emits two exact-head source
fixtures:

- one accepted transition;
- one deterministic rejection.

These verify request/result wiring, observation shape and comparator behavior.
They are explicitly not production traffic, recovery evidence, load evidence or
authority-cutover approval.

## Incident handling

On divergence:

1. stop promotion;
2. preserve both raw canonical results and exact binaries/commits;
3. preserve the Nakama authority-context fingerprint and request hash;
4. classify the first differing field;
5. determine whether the defect belongs to World rules, Nakama framing,
   contract interpretation or evidence tooling;
6. create a new vector for every confirmed contract defect;
7. rerun the full corpus from the beginning.

Do not route the affected match to World authority as a fallback. Rollback means
stopping new candidate admission while preserving the already selected
authority generation.
