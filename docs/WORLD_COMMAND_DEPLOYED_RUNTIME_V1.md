# TrillionniumGame World Command Deployed Runtime v1

Status: current implementation candidate; not a production or cutover claim.

Owner: `TrillionniumFoundation/TrillionniumGame`.

## Scope

This tranche connects the existing authority-safe World command coordinator to
the real Nakama match callback and server-owned storage surface. It deliberately
excludes Trillionnium Chain, Chain finality/proofs, CEX settlement, public
online, and public player markets.

Two explicit command profiles exist:

- `legacy_direct`: the grandfathered direct authoritative command path;
- `world_transition_v1`: the target path that calls an unsigned deterministic
  World transition service.

There is no automatic fallback from `world_transition_v1` to `legacy_direct`.
A target-profile configuration, World transport, verification, storage, or
stale-fence failure fails that runtime generation closed.

## Authority sequence

```text
signed Nakama command
  -> non-mutating core preflight
  -> durable reservation + attempt
  -> release Store critical section
  -> bounded HTTPS World execution
  -> independent result verification
  -> exact stale-fence revalidation
  -> core command apply
  -> one Nakama StorageWrite batch:
       authoritative core snapshot
       World command snapshot / receipt
  -> durable broadcast
```

World never receives or owns participant admission, participant roles,
canonical global event sequence, Nakama match version, command idempotency,
canonical roots, completion keys, or value settlement.

## Storage and crash semantics

Reservation and attempt changes use a dedicated server-owned Nakama storage
object with optimistic concurrency. The accepted commit uses one multi-object
`StorageWrite` call containing both the authoritative core snapshot and the
World command snapshot/receipt.

Before the storage call, any local encoding failure restores the exact
pre-commit core snapshot. Once `StorageWrite` has been invoked, an error or
malformed acknowledgement is treated as an ambiguous commit: the current
runtime generation terminates and both objects are reloaded from storage. It is
forbidden to restore an older in-memory state and continue after an ambiguous
storage acknowledgement.

The coordinator preserves the same reservation generation, canonical World
request, request hash, transition ID, and World command ID across transport
loss, cancellation, and response-loss retries.

## HTTPS boundary

The target executor requires:

- absolute HTTPS endpoint;
- TLS 1.3 minimum;
- explicitly pinned CA certificate;
- independent bearer credential;
- redirect rejection;
- exact canonical request bytes;
- bounded timeout and response bytes;
- `application/json` response;
- strict World transition verification before any local commit.

A connection loss after request transmission is classified as ambiguous and
keeps the original reservation identity.

## Completion

Target-profile completion requires:

- no pending World reservations;
- at least one accepted World transition receipt;
- terminal `outcome_hash` equal to the latest accepted World outcome hash.

Nakama still constructs canonical roots and signs `MatchCompletedV1`; World
supplies only unsigned deterministic game-domain material.

## Operator RPCs

- `trnm_world_command_ready_v1` exposes profile readiness without secrets.
- `trnm_world_command_status_v1` is operator-only and exposes bounded cursor,
  backlog, retry, state-hash, and latest accepted receipt metadata.
- `trnm_world_command_abort_v1` is operator-only and must bind the exact command
  ID and reservation generation. Abort retires a reservation; it cannot create
  an event, advance state, or manufacture a completion.

Published JSON Schemas are under `contracts/world-command-rpc-v1/`.

## Isolated process failpoints

The following failpoints exist only when all conditions hold:

```text
TRNM_WORLD_COMMAND_PROFILE=world_transition_v1
TRNM_WORLD_COMMAND_FAULT_LAB=1
TRNM_WORLD_COMMAND_FAILPOINT=<after_reservation|after_verify>
```

They terminate the Nakama process with a distinct exit code after the durable
reservation or after World verification and before commit. Any failpoint in
legacy mode, or without the explicit fault-lab flag, is rejected during
configuration.

## Promotion blockers

Source implementation is not deployed evidence. Promotion remains blocked on:

- exact-head Go tests, vet, race checks, and plugin/container build;
- PostgreSQL-backed Game + HTTPS World + response-drop proxy execution;
- process kills at both failpoints and successful restart recovery;
- multi-object storage atomicity inspection;
- `pg_stat_activity`/`pg_locks` evidence showing no business transaction held
  during external World wait;
- OCC conflict, duplicate race, poison isolation, and response-loss retry;
- representative accepted/rejected/restart/load corpus with zero unexplained
  deterministic divergence;
- 24-hour endurance;
- compatibility admission drain, target admission switch, and rollback
  rehearsal that never rewrites canonical history;
- independent Integration exact evidence lock.

Until all blockers close, `cutover_authorized`, closed-online promotion, public
online, and public player markets remain false.
