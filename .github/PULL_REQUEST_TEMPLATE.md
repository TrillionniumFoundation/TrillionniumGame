## Accountable scope

Describe one bounded capability or control-plane change. State what is deliberately excluded.

## Exact candidate

Do not copy a stale SHA before the final push. Before readiness, replace the placeholders from the exact GitHub PR head and candidate-identity artifact.

- repository: `TrillionniumFoundation/TrillionniumGame`
- base commit: `<40-char-sha>`
- head commit: `<40-char-sha>`
- head tree: `<40-char-sha>`
- candidate manifest artifact/digest: `<artifact-id> / sha256:<64-hex>`

- [ ] The body was refreshed after the latest push.
- [ ] The checked-out CI SHA equals the PR head.

## Plan and gap linkage

- plan version: `3`
- gap IDs:
- task IDs:
- parity/denominator leaf IDs:
- product/stage gates affected:
- compatibility profile and maximum claim:

## Implementation

List code, schema, configuration, protocol and documentation changes. Identify every first-party runtime/process boundary.

## Test and evidence matrix

| Layer | Exact command/workflow | Non-zero assertions | Artifact/evidence ID | Result |
|---|---|---:|---|---|
| Static/source |  |  |  |  |
| Rust/Python/Go unit |  |  |  |  |
| Property/fuzz |  |  |  |  |
| Live database |  |  |  |  |
| Wire/oracle differential |  |  |  |  |
| Fault/restart |  |  |  |  |
| Security/privacy |  |  |  |  |
| Operations/restore |  |  |  |  |

- [ ] Empty, skipped, cancelled, missing and older-head results are treated as failure to prove.
- [ ] Required live prerequisites fail rather than silently skip.
- [ ] Artifact SHA-256 values are indexed in `docs/evidence/index.json`.

## Data and authority

- schema/migration impact:
- migration-chain digest:
- current and target writer/owner:
- idempotency identity:
- rollback barrier:
- external effects/outbox behavior:

- [ ] No synchronous dual business write was added.
- [ ] External I/O is outside mutable database transactions.
- [ ] Stale revision/generation/lease paths fail closed.

## Security and privacy

- threat-model impact:
- key/secret domain impact:
- dependency/SBOM/provenance impact:
- fixture/PII handling:
- negative/fuzz vectors:

## Divergences and limitations

List every known difference from the pinned Nakama oracle, its P0/P1/P2 severity, owner and evidence status. Do not hide identity, ACL, sequence, money, version, cursor, error-code or durable-effect differences through normalization.

## Independent review

| Required role | Reviewer | Independent of implementation | Decision/evidence |
|---|---|---|---|
| General/CODEOWNER |  |  |  |
| Database |  |  |  |
| Security |  |  |  |
| Protocol/realtime |  |  |  |
| SRE/operations |  |  |  |

- [ ] P0/P1 review binds the exact final head and evidence artifacts.
- [ ] Approval from an older head is dismissed.
- [ ] The implementation author is not the sole approving reviewer.

## Claim boundary

Explicitly state the maximum valid result. Unless evidence-derived gates say otherwise, retain:

```text
SG0-SG9 = not earned
C1-C5 = not earned
complete Nakama compatibility = false
production-ready = false
public-online = false
drop-in replacement = false
Nakama retired = false
```

## Merge readiness

- [ ] `trillionnium-game-merge-gate` is non-empty, terminal and successful on the exact head.
- [ ] Every path-specific required lane is successful.
- [ ] Dependencies and child P0/P1 divergences are closed or remain explicit blockers.
- [ ] Required independent reviews are accepted.
- [ ] Conversations are resolved.
- [ ] The branch is current with protected `main`.
- [ ] No self-merge is requested or performed.
