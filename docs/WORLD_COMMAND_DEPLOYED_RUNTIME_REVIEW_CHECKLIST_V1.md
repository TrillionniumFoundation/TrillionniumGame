# World command deployed runtime review checklist v1

Status: current
Owner: TrillionniumGame runtime
Scope: target-profile World transition execution; Trillionnium Chain is excluded.

## Source invariants

- [ ] `core.PreflightCommand` performs signature, identity, sequence and version checks without mutation.
- [ ] reservation persistence completes before external World execution.
- [ ] World execution occurs outside the core mutex and outside a storage/database transaction.
- [ ] verified accepted results use `CommitWith` and one Nakama `StorageWrite` batch for the core snapshot and World journal.
- [ ] any missing or ambiguous acknowledgement terminates the runtime generation instead of reporting success.
- [ ] exact replay returns the original receipt/event without another World request.
- [ ] stale match version, global sequence, participant cursor, state revision/hash/tick or reservation generation produces no canonical advancement.
- [ ] target-profile failure never falls back to the legacy direct path.
- [ ] completion outcome is bound to the latest accepted World outcome hash.

## Exact-head automated gates

- [ ] `gofmt -l runtime` is empty.
- [ ] `go test ./... -count=1` passes.
- [ ] race tests for `internal/worldcommand` and `internal/worldtransition` pass.
- [ ] `go vet ./...` passes.
- [ ] plugin and three fault-lab binaries build.
- [ ] authority/source checker passes.
- [ ] malicious negative fixtures fail closed.
- [ ] fault-lab PostgreSQL binding checker passes.

## Isolated deployed evidence

- [ ] PostgreSQL, Nakama/TrillionniumGame, TLS World fixture and response-drop proxy start from the exact head.
- [ ] happy accepted transition produces one event, one receipt and matching deterministic state.
- [ ] response loss after remote success reuses the same request identity and converges exactly once.
- [ ] external wait has no PostgreSQL transaction older than the bounded probe threshold.
- [ ] process exit after durable reservation recovers the original reservation.
- [ ] process exit after verified World result recovers and commits exactly once.
- [ ] every scenario contains a core/World storage atomicity record and artifact hashes.

## Promotion blockers

The following remain false until independently reviewed exact-head and deployed evidence exists:

```text
cutover_authorized
closed_online_promoted
public_online_enabled
public_player_market_enabled
```

This checklist does not grant Chain finality, inclusion-proof, CEX settlement,
multi-host fencing, 24-hour endurance, compatibility admission drain or
rollback-rehearsal credit.
