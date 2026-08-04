# Authoritative match evidence v1

Status: P0 contract. Namespace: `trnm_nakama_authoritative_match_v1`.

This contract covers admission, authoritative command ordering, event evidence,
and a signed match-completion record for one fixed two-participant match. It
does not define gameplay rules, Hepta evaluation, Chain inclusion/finality, or
settlement.

## Security and ownership

- Hepta signs each `trnm.match.authorization.v1` claim with a trusted Ed25519
  issuer key. A claim binds one Nakama user, logical match, participant slot,
  role, agent key snapshot, ruleset, dataset, and challenge snapshot.
- First use must be inside the signed validity interval. Once durably consumed,
  an exact retry by the same subject may resume after expiry; a different user,
  claim, or slot is rejected. This avoids a remote consume/write crash window.
- Agent commands are Ed25519-signed with the key frozen in the authorization.
  The server derives the participant from the authenticated presence and never
  trusts a client-supplied roster or slot in isolation.
- Completion is operator-signalled and Nakama-signed. A client command cannot
  force completion. Chain adapters may later submit the result using external
  key namespace `nakama.commitment`; Chain ingress and finality remain outside
  this repository.
- `authority_public_key_base64` in an evidence response is diagnostic key
  material, not a trust anchor. A verifier must resolve `authority_key_id`
  through an externally pinned, versioned authority registry or deployment
  configuration, require the resolved key to equal the returned key, and only
  then verify the completion signature. Trusting the response's bundled key by
  itself would merely prove that the response is self-consistent.
- All SHA-256 JSON values are exactly `sha256:` followed by 64 lowercase hex
  characters. JSON byte arrays use standard padded base64.

Unknown JSON fields are rejected. Identifiers and roles must be non-empty,
valid UTF-8, at most 512 Unicode code points, and must not contain NUL. Logical
`match_id` is additionally restricted to
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` so its public representation and Nakama
storage key cannot diverge. The roster is exactly slots 1 and 2 with unique
Nakama user id, agent id, agent DID, agent key id, and agent public key.

Opaque command and event payloads are standard padded base64 in JSON. The
encoded schema ceiling is 87,384 characters; after decoding, the runtime
enforces a payload size from 1 through 65,536 bytes.

One logical P0 match accepts at most 512 distinct commands and at most 2 MiB of
decoded command payload in total. Exact idempotent replay does not consume a
second quota entry. A command that would exceed either limit is rejected before
any authoritative mutation; these bounds ensure a previously accepted state
can still produce durable terminal evidence.

## RPC wire surface

All RPC payloads and successful results use the self-contained schemas under
`contracts/v1/`. RPC transport errors are Nakama errors and are not disguised
as successful result objects.

| RPC id | Request schema | Successful response schema |
| --- | --- | --- |
| `trnm_match_create_v1` | `create-match-request` | `create-match-response` (`match-runtime`) |
| `trnm_match_resume_v1` | `resume-match-request` | `resume-match-response` (`match-runtime` or immutable `evidence`) |
| `trnm_match_complete_v1` | `complete-match-request` | `complete-match-response` (`evidence`) |
| `trnm_match_evidence_v1` | `evidence-request` | `evidence-response` |
| `trnm_health_v1` | no fields are consumed | `health-response` |
| `trnm_ready_v1` | no fields are consumed | `readiness-response` |

Create accepts exactly two signed authorizations, one for each participant
slot, and is operator-only. Resume and complete are also operator-only.
Evidence retrieval accepts either an operator token or the authenticated
participant's authorization id. Supplying neither is invalid; an
authorization id alone is not a bearer credential because the runtime also
binds it to the authenticated Nakama user.

`logical_match_id` is the durable identity. `external_match_id` identifies one
ephemeral Nakama runtime generation and may change after resume. A completed
resume returns evidence instead of creating a new runtime. Completion facts
contain only `result_code`, `winner_slot` (`0` means no winner), and
`outcome_hash`; they never contain derived roots.

Health proves liveness only. Readiness separately reports configuration,
database, and server-owned storage checks; an unready process returns a
structured response with `ready=false` rather than a transport error.

## Realtime match surface

Join the returned `external_match_id` with metadata matching
`join-metadata.schema.json`. Its sole field is `authorization_id`. The runtime
combines that identifier with the authenticated Nakama user id; clients cannot
select a participant slot or user through metadata. The first accepted join is
durably recorded before its event is sent. A reconnect with the same consumed
authorization is accepted idempotently and does not create or replay a second
join event.

The authoritative match uses these reliable realtime opcodes:

| Opcode | Direction | JSON schema | Recipients and meaning |
| ---: | --- | --- | --- |
| 1 | client to server | `agent-command` | Submit one signed command. Other client opcodes are rejected. |
| 2 | server to client | `match-event` | A newly committed join or command event goes to all current presences. An exact command retry receives its original event only at the requesting presence. |
| 3 | server to client | `command-rejected` | Ephemeral rejection sent only to the command sender; it is not part of the event archive. |
| 4 | server to client | `match-completed` | Final signed completion sent to all current presences after durable persistence. |

The runtime requests Nakama's reliable delivery flag, but realtime delivery is
not an application acknowledgement and there is no offline broadcast queue.
Every accepted mutation is persisted before broadcast, and a broadcast failure
never rolls authority back. Clients must treat `command_id` as an idempotency
key and reconcile durable completion through `trnm_match_evidence_v1`.

Exact command replay is reachable only while that authoritative runtime
instance remains active. Completion persists evidence, broadcasts opcode 4,
and terminates the instance. A retry after completion therefore uses the
evidence RPC; it does not resubmit opcode 1 or recreate an event.

## Canonical primitive framing

Canonical frames are language-neutral binary data:

- a frame starts with its ASCII domain followed by one NUL byte;
- a string is UTF-8 encoded and then framed as bytes;
- bytes are `u32_be(length) || raw_bytes`;
- `u32` and `u64` are unsigned big-endian integers;
- `i64` uses the big-endian two's-complement bit pattern;
- a digest field is the 32 raw SHA-256 bytes, without the `sha256:` text.

Fields are appended in the order below. JSON object key order is never
canonical and must not be signed or hashed.

The fixed inputs and expected bytes in `golden-vectors.json` cover every frame
below. Its deterministic private seeds are explicitly test-only and must never
be used as production keys.

### Authorization

The claim frame starts with `trnm_match_authorization_claim_v1\0`, then:

`schema, authorization_id, match_id, challenge_id, agent_id, agent_did,
agent_key_id, agent_public_key, subject_user_id, participant_slot, role,
ruleset_hash, dataset_hash, challenge_snapshot_hash, issued_at_unix,
expires_at_unix`.

The signed message is a `trnm_match_authorization_signature_v1` frame containing
`issuer_key_id` and the length-prefixed claim frame. The signature is Ed25519.

### Command

The command message starts with `trnm_match_command_signature_v1\0`, then:

`schema, command_id, authorization_id, match_id, challenge_id, agent_id,
participant_slot, participant_sequence, expected_match_version, issued_at_unix,
payload_type, payload, payload_hash, agent_key_id`.

The command fingerprint is SHA-256 of a
`trnm_match_command_fingerprint_v1` frame containing the length-prefixed command
message and signature. An exact duplicate command id returns the original
event; the same id with a different fingerprint is a conflict.

### Events and Merkle root

An event hash is SHA-256 of a `trnm_match_event_v1` frame containing:

`schema, event_id, event_type, match_id, challenge_id, sequence, causation_id,
occurred_at_unix, participant_slot, match_version, payload_type, payload,
payload_hash`.

The canonical event id is:

```text
event_id = SHA256(
  "trnm_match_event_id_v1\0" ||
  bytes(match_id) || bytes(causation_id) || u64_be(sequence)
)
```

`event_id` uses the normal `sha256:<lowercase hex>` JSON representation. Match
version starts at 1 before any event, so every persisted event must satisfy
`match_version = sequence + 1`.

The two admission events precede commands. A `participant_joined` event uses
the authorization id as `causation_id` and payload type
`trnm.participant.joined.v1`. Its payload is the binary frame:

```text
"trnm_participant_joined_v1\0" || participant_slot_u32_be ||
bytes(subject_user_id) || bytes(authorization_id) || bytes(agent_id)
```

Thus the minimal completed P0 archive contains two unique participant joins
(in arrival order), at least one command, then authoritative completion. The
golden fixture freezes the concrete slot 1, slot 2, command, completion
four-event path; the live runtime also permits slot 2 to arrive first.

For event sequence `s` and raw event hash `h`:

```text
leaf = SHA256("trnm_match_event_leaf_v1\0" || u64_be(s) || h)
node = SHA256("trnm_binary_merkle_node_v1\0" || left || right)
```

Events are ordered by a gapless sequence starting at 1. At an odd Merkle level,
the last node is duplicated. There is no root for an empty archive.

### Roster root

Sort the two entries by slot and hash a `trnm_match_roster_v1` frame containing
`u32_be(2)`, followed for each entry by:

`participant_slot, subject_user_id, agent_id, agent_did, agent_key_id,
agent_key_hash, role`.

This binary frame replaces the legacy practice of relying on JSON field order.

### Archive hash

The canonical archive is a `trnm_match_event_archive_v1` frame containing the
event count as `u64`, followed for each gapless event by the length-prefixed
event facts frame and the length-prefixed raw event hash. `archive_hash` is the
SHA-256 digest of these full archive bytes, never a URI or filename.

### Completion without self-reference

Terminal facts are `result_code, winner_slot, outcome_hash` framed under
`trnm_match_terminal_facts_v1`. They explicitly exclude `event_root`,
`roster_root`, and `archive_hash`. Nakama first seals that terminal event, then
computes all derived roots. This ordering prevents an event-root self-reference.

The completion signature uses a `trnm_match_completed_signature_v1` frame:

`schema, commitment_id, match_id, challenge_id, terminal_facts_frame,
event_count, event_root, roster_root, ruleset_hash, dataset_hash,
challenge_snapshot_hash, archive_hash, completed_at_unix, authority_key_id`.

`terminal_facts_frame` is the length-prefixed canonical
`trnm_match_terminal_facts_v1` frame. `MatchCompletedV1` carries the same
terminal facts as a required JSON object. Their inclusion in the signature
binds the human-readable result directly to the evidence roots.

`commitment_id` is the digest of a `trnm_match_commitment_id_v1` frame containing
`match_id`, `event_root`, and `archive_hash`.

The response field `authority_public_key_base64` allows consumers to diagnose
key mismatches and reproduce verification. It never replaces external trust in
the `authority_key_id` mapping. Consumers must reject an otherwise valid
signature when that mapping is absent, unpinned, expired for the evidence
epoch, or resolves to a different public key.

## Restart and resume semantics

Open-source Nakama keeps an authoritative match instance on one host in memory.
P0 therefore persists an authority-signed logical match snapshot, consumed authorizations,
command fingerprints, ordered events, and completion evidence in server-owned
storage after each transition. After a process restart, `trnm_match_resume_v1`
creates a new external Nakama match instance for the same logical `match_id`.
The external instance id may change; logical ids, sequences, roots, and signed
evidence must not.

P0 enforces one active writer by optimistic storage version. A conflict fails
closed; it is not multi-host failover or distributed fencing.

## Integration status

Passing this repository's P0 gate proves a real Nakama runtime slice only.
Trillionnium Integration must remain `blocked` / `runnable=false` until an
immutable compatible Chain revision provides canonical ingress and a receipt
verifiable against CometBFT finality and AppHash, and Hepta issues the signed
authorization form defined here.
