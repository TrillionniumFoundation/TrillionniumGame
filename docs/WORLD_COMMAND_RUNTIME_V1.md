# World command runtime v1

Status: implemented locally; exact-head remote CI and deployed fault evidence pending.

Owner: TrillionniumGame / Nakama authoritative runtime.

## Purpose

This tranche provides the durable, authority-safe command coordinator between
Nakama-owned match authority and the unsigned `trnm_world_transition_v1`
deterministic rule boundary.

The repository was renamed from `Trillionnium-Nakama` to `TrillionniumGame`.
The Go module path remains the legacy path during this compatibility tranche;
exact repository evidence and future locks must use
`TrillionniumFoundation/TrillionniumGame`.

## Invariants

1. Nakama owns admission, participant sequence, match version, canonical global
   event sequence, idempotency, durable recovery and completion signing.
2. World receives only a deterministic prior state, command, selected ruleset,
   content revision and expected tick.
3. The coordinator persists a reservation before external execution.
4. Ambiguous response loss, cancellation and retryable rejection reuse the
   exact canonical request, request hash, transition ID and World command ID.
5. A verified result is committed only if match version, global sequence,
   deterministic state revision/hash/tick and participant sequence still equal
   the captured fence.
6. A stale result causes zero state, sequence or receipt advancement and is
   retained as a retired audit generation.
7. A committed duplicate returns the original receipt without calling World.
8. A command ID reused with different intent fails closed.
9. Operator abort is generation-bound and cannot create an applied event,
   deterministic state change, completion or settlement claim.
10. Public online, public player markets and authority cutover remain disabled.

## Packages

`runtime/internal/worldcommand` contains:

- a pure prepare → execute → verify → commit coordinator;
- retry and ambiguous-outcome classification;
- a CAS-backed persistent reservation/receipt journal;
- restart-safe canonical World request identity;
- generation takeover and stale-generation retirement;
- exact state/version/sequence/tick fences;
- status and bounded failure evidence;
- a standard adapter for `runtime/internal/worldtransition`;
- deterministic in-memory backend and fault-matrix tests.

The coordinator package has no direct socket, database, signer, wallet or
completion-signing capability. External execution is supplied through the
narrow `Executor` interface and persistence through `SnapshotBackend`.

## Durable record

Each pending reservation binds:

- client command ID and canonical intent fingerprint;
- authenticated user and participant identity;
- participant input sequence;
- Nakama authority context;
- deterministic state revision/hash/tick;
- match version and next global event sequence;
- canonical command bytes;
- canonical World request bytes and request hash;
- retry-stable World transition and command IDs;
- reservation generation and state token;
- attempt history and bounded failure classification.

Receipts bind the same request identity plus final disposition and the exact
post-commit match/state cursor. Non-retryable deterministic rejection produces
a receipt without advancing authoritative state. Retryable rejection remains a
pending reservation.

## Fault matrix implemented in source tests

- kill/restart after reservation persistence;
- ambiguous remote success / response loss with byte-identical retry;
- exact committed duplicate without a second World call;
- same command ID with different intent;
- two distinct reservations racing for one next global sequence;
- stale result with zero canonical mutation;
- generation takeover and previous-generation rejection;
- persistence failure with unchanged in-memory state and pending reservation;
- retryable rejection and invalid-result isolation;
- cancellation before external execution;
- non-retryable rejection with zero state advancement;
- generation-bound operator abort;
- corrupted snapshot rejection.

The workflow also runs the race detector. Source tests are not a substitute for
process-kill, network-proxy, real PostgreSQL/Nakama storage or cross-host
failure evidence.

## Remaining deployment evidence

- real World HTTPS executor with response-loss proxy;
- process kill after durable reservation and after remote success;
- Nakama storage multi-object atomicity and OCC conflict evidence;
- two live workers retrying one reservation;
- restart with pending and committed reservations;
- representative accepted/rejected/load shadow corpus;
- backlog convergence after prolonged World outage;
- compatibility-match drain;
- cutover and rollback rehearsal;
- exact Integration component lock for the final source and evidence heads;
- exact-head GitHub Actions conclusions.

Until those rows pass, activation is `shadow_only`, cutover is unauthorized,
public online is NO-GO and the public player market remains disabled.

## Machine-readable source evidence

`tools/summarize_world_command_faults.py` consumes the exact `go test -json`
stream and refuses to pass unless every required fault scenario is present and
passed. The resulting `trnm_game_world_command_fault_evidence_v1` packet binds:

- canonical repository `TrillionniumFoundation/TrillionniumGame`;
- exact 40-hex commit and Git tree;
- SHA-256 of the raw Go event stream;
- every required scenario and its terminal state;
- fail-closed authority/release flags;
- explicit source-only limitations.

The packet contract is
`contracts/world-command-fault-evidence-v1.schema.json`. A source packet cannot
be promoted into deployed fault, cutover, closed-online or public-online credit.

## Audit treatment of retries

A committed receipt retains all execution attempts for its reservation
generation. The winning attempt is marked committed; concurrent in-flight
attempts are closed as superseded by that same canonical result. Stale,
operator-aborted and generation-takeover paths close pending attempts with a
stable failure class before moving them into the retired audit set.

The client command intent fingerprint deliberately excludes mutable authority
cursors such as match version, global sequence, state revision/hash and tick.
It remains bound to the command bytes, user, participant, participant sequence,
match, authorization, roster, ruleset and content revision. Consequently an
exact committed duplicate can replay its original receipt after authority state
advances, while any changed signed intent still fails closed.
