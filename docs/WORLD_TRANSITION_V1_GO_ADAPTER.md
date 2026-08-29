# World transition v1 Go consumer

Status: implemented source candidate; exact-head CI required.

## Purpose

`runtime/internal/worldtransition` is the production-repository-language
consumer for the exact World contract `trnm_world_transition_v1`. It is an
independent implementation: it does not import the Python reference adapter,
World source code, a sibling checkout, a database, a signer, a network client,
wall-clock time or randomness.

The package prepares an unsigned deterministic request from an authority context
that Nakama already owns, verifies the complete accepted/rejected World result,
and emits typed observations for shadow comparison. It does not itself admit a
participant, reserve a global sequence, persist a command, sign completion or
authorize cutover.

## Canonical profile

The Go parser requires exact UTF-8 canonical JSON:

- object and array roots where the contract requires containers;
- object keys strictly ascending by decoded UTF-8 bytes;
- no duplicate keys;
- signed 64-bit decimal integers only;
- no floating point, exponent form, leading zero or `-0`;
- no UTF-8 BOM, insignificant whitespace or alternate string escaping;
- maximum nesting depth 128;
- exact byte-for-byte re-encoding before acceptance.

Payload bytes and all World domain hashes are independently recomputed.

## Authority context

The input context binds:

- logical match identity;
- consumed authorization identity;
- participant roster commitment;
- Nakama match version;
- next canonical global event sequence;
- command idempotency identity;
- exact ruleset/content revisions;
- expected deterministic tick.

These values derive opaque retry-stable `transition_id` and `command_id` values.
They are not copied into the World payload as participant, roster, ordering or
idempotency authority.

## Verified output

Accepted output verification binds:

- exact contract/ruleset/content/transition identities;
- exact request and previous-state hashes;
- non-regressing deterministic tick;
- next-state, replay and optional outcome payload hashes;
- World outcome hash;
- World transition hash over the exact accepted facts.

Rejected output verification binds the exact request, one stable error code,
its fixed retry policy and bounded diagnostic text. Unknown result fields,
unknown error codes, altered hashes and nested authority surfaces fail closed.

## Integration boundary

This package is not the command-loop integration. Production execution still
requires:

```text
persist immutable reservation
  -> release authority lock and storage transaction
  -> execute World externally
  -> verify exact result
  -> re-enter authority state
  -> reject stale version/sequence/state/reservation generation
  -> atomically commit state, event and receipt
```

A missing or failed executor must not fall back to World-local authority. A
matched shadow report never grants completion-signing, cutover, closed-online,
public-online or player-market approval.
