---
status: current
owner: trillionnium-nakama
contract_version: trnm_nakama_world_transition_adapter_v1
world_contract: trnm_world_transition_v1
world_commit: 0d7666d4d830fa8e56c78b23d438856064182535
world_tree: 1619ae76fa62a5e67bc7ff94429c62eea35deb87
applies_to:
  - WORLD-P0-003
  - nakama-realtime
  - shadow-verification
last_reviewed: 2026-08-28
review_due: 2026-09-11
---

# Nakama adapter for World transition v1

## Status

The adapter is **source implemented / shadow candidate**. It is not yet a
deployed Nakama runtime module, canonical completion signer, or authority
cutover.

The exact World source is pinned in
`contracts/world-transition-v1-consumer-lock.json`.

## Ownership

Nakama already owns the authority context supplied to this adapter:

- authenticated match and authorization identity;
- participant roster commitment;
- match version;
- canonical global event sequence;
- command idempotency key;
- selected World ruleset and content revisions;
- expected deterministic tick.

World receives none of those authority-bearing values directly. They are used
only to derive opaque, retry-stable `transition_id` and `command_id` values.

World owns only:

- validation and interpretation of the selected game-domain rules/content;
- deterministic state transition;
- unsigned next-state, replay and optional outcome material;
- World request, transition and outcome hashes.

## Data flow

```text
Nakama authoritative context
        |
        | prepare_world_transition()
        | - validate authority context
        | - canonicalize state/command
        | - derive opaque stable IDs
        v
trnm_world_transition_v1 request
        |
        | World deterministic rules
        v
unsigned accepted/rejected result
        |
        | verify_world_result()
        | - exact canonical bytes
        | - exact field set
        | - payload SHA-256
        | - request/transition/outcome hashes
        | - stable rejection semantics
        | - authority-field rejection
        v
VerifiedWorldTransition
        |
        +--> Nakama-owned persistence/order/recovery work (future slices)
        +--> shadow observation
```

The adapter does not mutate Nakama's authority context. Its verified result
retains the pre-existing Nakama global sequence, match version, roster hash and
idempotency key as context; no World field can replace them.

## Canonical JSON

`runtime/world_transition_v1/canonical.py` independently validates exact
canonical JSON:

- UTF-8 with no BOM or trailing whitespace;
- object keys strictly ascending by UTF-8 bytes;
- no duplicate keys;
- no insignificant whitespace;
- signed i64 decimal integers only;
- no floats, exponent forms, `-0` or overflow;
- no non-minimal string escaping;
- maximum nesting depth 128;
- object/array root for game payloads and transition messages.

The adapter recomputes the SHA-256 of every game payload and all published
World hash domains.

## Request preparation

`prepare_world_transition()` takes one immutable
`NakamaAuthorityContext`. It derives:

```text
transition_id =
  "wtx-" + prefix48(
    SHA256("trnm.nakama.world.transition.id.v1\n" || authority_binding)
  )

command_id =
  "wcmd-" + prefix48(
    SHA256("trnm.nakama.world.command.id.v1\n" || authority_binding)
  )
```

The authority binding includes match, authorization, roster, match version,
global event sequence and idempotency identity, but only the opaque IDs enter
the World request.

Retries and restart recovery must reuse the same persisted context and
canonical request bytes. `prepared_from_canonical_request()` verifies that a
recovered request is still bound to the exact authority context.

## Result verification

Accepted results are rejected unless all of the following remain exact:

- contract, transition, ruleset and content identity;
- request hash;
- previous-state hash;
- non-regressing tick;
- next-state, replay and optional outcome payload hash;
- World outcome hash;
- World transition hash;
- accepted/rejected field set;
- absence of nested authority fields.

Rejected results must:

- use one published stable code;
- bind the exact request hash;
- use bounded control-free detail;
- set `retryable=true` only for `internal_unavailable`.

## No authority promotion

This package contains no HTTP, socket, database, signer, wallet, finality or
private-key dependency. It does not:

- admit players;
- allocate canonical global order;
- persist canonical idempotency;
- recover a canonical match;
- produce canonical event/roster/archive roots;
- construct or sign `MatchCompletedV1`;
- settle value;
- enable public online.

Those are independent Nakama/Integration/CEX/Chain slices and gates.

## Validation

The dedicated workflow runs:

```text
boundary and exact World blob check
forbidden-capability negative fixtures
Python compile
unittest suite
exact-head synthetic accepted/rejected fixture emission
shadow comparison
artifact upload
```

Synthetic fixtures prove contract wiring only. Representative live accepted,
rejected, restart and load corpora remain required before cutover.
