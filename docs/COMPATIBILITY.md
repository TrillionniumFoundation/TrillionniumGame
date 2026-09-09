# Compatibility program

Status: **authoritative current documentation**  
Revision: 2026-09-01

## 1. Baseline

The initial compatibility baseline is immutable until an approved upstream-delta process changes it:

- `heroiclabs/nakama` tag `v3.40.0`;
- Nakama commit `d4d92f93f78bbbe62c7fc50a3f85c772ec121a09`;
- Nakama tree `f3c9cfc2726d5543da1564629170f35b98e3797d`;
- `heroiclabs/nakama-common` tag `v1.47.0`;
- nakama-common commit `449b77ecc8789aa466c36b67f6e498033dfcd9c5`.

Exact source, blob, image and toolchain identities are recorded in `docs/development/UPSTREAM_BASELINE.json` and denominator manifests. A tag or copied interface without the pinned object identity is insufficient.

## 2. Compatibility levels

| Level | Meaning | Required evidence |
| --- | --- | --- |
| C0 | planning/build/schema candidate | pinned source, reproducible build, controlled scope/status |
| C1 | wire-compatible subset | exact HTTP/gRPC/RT bytes, status, headers, details and close differential |
| C2 | behavior-compatible domain | C1 plus DB effects, hooks, concurrency, faults and restart |
| C3 | data-migration compatible | repeatable migration, semantic comparison and rollback barrier |
| C4 | operationally replaceable | HA, security, capacity, backup/PITR, upgrade and runbooks |
| C5 | supported full replacement | all mandatory leaves, migration, cutover and retirement complete |

Levels are capability and profile scoped. A Healthcheck source implementation or one matching storage vector does not grant repository-wide C1.

## 3. Denominator model

The human D0–D8 model is expanded into 14 machine review families:

| D class | Review families | Coverage |
| --- | --- | --- |
| D0 | `DEN-SOURCE` | repositories, commits, trees, blobs, images and toolchains |
| D1 | `DEN-API` | HTTP, gRPC, messages, enums and JSON mapping |
| D2 | `DEN-RTAPI` | realtime envelopes, CIDs, errors and socket lifecycle |
| D3 | `DEN-CONSOLE` | Console API, ACL actions and operator workflows |
| D4 | `DEN-RUNTIME` | initializers, hooks, contexts, match interfaces and module APIs |
| D5 | `DEN-CONFIG`, `DEN-CLI` | keys, defaults, precedence, validation, flags and exit codes |
| D6 | `DEN-DB`, `DEN-DATA` | migrations, schema objects and durable invariants |
| D7 | `DEN-METRICS`, `DEN-OPS`, `DEN-SDK` | signals, lifecycle, packaging and consumer behavior |
| D8 | `DEN-PROVIDERS`, `DEN-IAP` | provider states, callbacks, retries and value effects |

The current review worklist contains 14 materialized candidate families, 10,173 proposed leaves and 15 manual blockers. It remains `awaiting-independent-review`: all candidates are conservative proposals, not accepted denominators. SG1 remains incomplete until every family and the global bundle are independently accepted and locked.

The machine sources are:

- `docs/development/PARITY_DENOMINATORS.json`;
- `docs/development/DENOMINATOR_CLASSIFICATION_RULES.json`;
- `manifests/upstream/candidates/`;
- `manifests/upstream/review-requests/`;
- `manifests/upstream/denominator-review-worklist.json`;
- `docs/development/FEATURE_PARITY_MATRIX.md` as a generated human roll-up only.

## 4. Leaf contract

Every mandatory leaf records:

- stable leaf ID and denominator family;
- exact upstream repository/commit/tree/path/blob or image/toolchain identity;
- normalized signature hash;
- classification and rationale;
- owner role and implementation task;
- compatibility profile;
- test ID and required evidence types;
- implementation, verification and production status;
- evidence references and review decisions.

A denominator decrease, merge, exclusion or normalization requires an upstream delta or approved decision. Trillionnium extensions use separate extension IDs and cannot improve Nakama parity coverage.

## 5. Profiles

Compatibility behavior and hardened native behavior are explicit profiles. A hardened change may intentionally reject behavior accepted by upstream, but it cannot be reported as exact compatibility. Profile selection, defaults and unsupported combinations are denominator fields.

PostgreSQL and CockroachDB are separate support profiles. JSON and protobuf realtime are separate wire profiles. Runtime engines, provider integrations and official SDK consumers receive separate conclusions.

## 6. Oracle lanes

Two oracle classes are retained:

1. **immutable oracle** — unmodified official Nakama artifact at the pinned identity;
2. **instrumented oracle** — minimal audited patches for deterministic clock/random/provider/DB or trace capture.

The instrumented oracle must prove equivalence outside the declared injected fields. Oracle and candidate use isolated writable clones generated from the same fixture seed.

## 7. Differential capture

Applicable comparisons include:

- raw request and response bytes;
- HTTP/gRPC status, headers and details;
- WebSocket opcode, envelope, CID, close and reconnect behavior;
- database rows and reconstructable invariants;
- hook order, context and results;
- events, notifications and outbox intents/receipts;
- logs, metrics and lifecycle state;
- provider requests, ambiguous outcomes and reconciliation.

Every input, output, environment and normalizer decision is archived by digest.

## 8. Normalization policy

Only fields explicitly proven non-contractual may be normalized. The following are never normalizable:

```text
identity
authorization and ACL
money or value state
revision, generation and sequence
version and cursor
public error code
transaction receipt
external durable effect
```

An unexplained P0/P1 divergence blocks promotion. A documented difference is not automatically an accepted extension.

## 9. Official SDK matrix

Official SDK tests are black-box consumers of the public candidate. The matrix covers supported language/version combinations, auth/session flows, HTTP/gRPC mapping where exposed, realtime JSON/protobuf, reconnect, storage versions, social/match behavior and public errors. Generated internal tests cannot substitute for official consumer behavior.

## 10. Storage public version and OCC

For the pinned Nakama storage profile, the public object version is MD5 over the exact stored value bytes, encoded as 32-character lowercase hexadecimal. This is a wire and optimistic-concurrency compatibility requirement only. MD5 receives no security or integrity-authentication credit; internal integrity, tamper evidence and cryptographic digests use separate strong types and algorithms.

Client write conditions remain distinct:

- **blind write** — no expected public version is supplied;
- **create-only** — the special creation condition requires the object not to exist;
- **exact expected version** — the supplied 32-character lowercase public version must match the currently stored value.

Empty, wildcard and exact-version behavior, batch atomicity, ACL effects, value bytes, returned version and database mutations require immutable-oracle differential evidence. The standalone storage-version adapter is a source candidate and cannot by itself establish storage behavior compatibility or database durability.

## 11. Current source frontier

Current source candidates include:

- authority generation/revision and receipt semantics;
- session-family and access-token verification slices;
- storage public-version and OCC candidates;
- bounded HTTP framing and stable transport errors;
- PostgreSQL/CockroachDB schema and transaction slices;
- transactional outbox lease/reclaim/final-attempt behavior;
- a bounded persistent WebSocket JSON and narrow protobuf envelope;
- the exact Nakama Healthcheck method signature and generated gRPC source;
- query and presence cores.

Open compatibility work includes most Nakama HTTP/gRPC/RTAPI, Console, Runtime, providers/IAP, social, leaderboards, matchmaker, multiplayer, official SDK differential, distributed ownership and complete migration behavior.

## 12. SG1 and SG2 acceptance

SG1 requires all denominator families non-empty, reproducible, fully classified, mapped and independently locked with zero unclassified leaves. SG2 additionally requires reproducible immutable and instrumented oracles, an approved normalizer registry, differential engine, fixture isolation and consumer matrix.

Generated bundles, author approval, automation comments and old-head reviews do not satisfy either gate.

## 13. Claim boundary

Current candidate manifests and successful source tests provide no automatic compatibility credit. Complete Nakama compatibility, repository-wide C1–C5, SG1–SG9, production readiness, public-online approval, drop-in replacement and retirement remain false until evidence-derived gates say otherwise.
