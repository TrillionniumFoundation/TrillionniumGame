# Nakama adapter for World transition v1

Status: Python and Go consumer implementations are present; exact-head CI,
production Store integration and live callback routing remain required.

## Authority boundary

Nakama owns authenticated admission, participant identity and roles, logical
match lifecycle, canonical global order, match version, command idempotency,
restart recovery, event/roster/archive roots and `MatchCompletedV1` signing.

World receives only an unsigned deterministic transition request and returns
unsigned deterministic state, replay and optional outcome material. World
cannot set Nakama sequence, match version, idempotency, roster, completion
signature, Chain finality or wallet state.

## Implementations

Two independent consumers intentionally coexist:

- `runtime/world_transition_v1/` — standard-library Python reference and shadow
  tooling;
- `runtime/internal/worldtransition/` — standard-library Go implementation used
  by the production repository language.

Both bind the exact World PR #21 contract and independently enforce canonical
JSON, payload budgets, request/transition/outcome domains, stable rejections and
recursive authority-surface denial. Neither implementation performs network or
database I/O.

## Required production orchestration

The Go consumer is not permission to call World from inside `core.Engine` or a
storage transaction. The next production slice must use:

```text
prepare and persist immutable reservation under Nakama authority
  -> release mutex and storage/database transaction
  -> execute exact World request
  -> verify accepted/rejected result
  -> re-lock
  -> reject stale match version, global sequence, state revision/hash/tick,
     reservation generation or idempotency identity
  -> atomically commit canonical event, state and receipt
```

Ambiguous transport outcomes and cancellation preserve the original request
identity. A verified remote result must still pass stale fencing before it can
mutate canonical state. There is no automatic fallback to World-local authority.

## Current non-creditable gaps

- production `worldcommand.Store` adapter;
- durable reservation snapshot/restore and restart takeover;
- target authority-profile match-loop route;
- live World executor transport and timeout policy;
- concurrent command, response-loss and crash-point matrix;
- representative cross-repository shadow/load corpus;
- Integration final exact component lock;
- drain, cutover and rollback rehearsal.

Public online, public player markets and authority cutover remain disabled.
