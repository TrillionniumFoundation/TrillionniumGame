# Paper Raid research session v1

Status: P0/P2 contract. Namespace: `trnm_nakama_research_session_v1`.

This contract adds a cooperative 3–5 participant Paper Raid session beside the
unchanged `trnm_nakama_authoritative_match_v1` contract. It owns admission,
presence, ordered Agent actions, reconnect/archive catch-up, roster replacement,
and signed cooperative completion. It does not own paper bytes, Hepta research
state/evaluation, human authorship consent, Chain finality, or rewards.

## Invariants

- A roster contains 3–5 gapless slots ordered `1..N`. Authorization id, user,
  Agent id, DID, key id, and raw Ed25519 public key are unique.
- Every Hepta-signed authorization binds `session_id`, `team_id`,
  `paper_project_id`, `challenge_id`, a roster epoch/root, immutable ruleset and
  challenge hashes, one Nakama user, one external Agent key, and one role.
- `subject_user_id` is the exact Nakama UUID returned by Nakama authentication
  and must equal `presence.GetUserId()`. It is not an OIDC/Consumer subject.
  Hepta/Consumer Edge owns the trusted external-subject to Nakama-user binding.
- A fresh session starts at `roster_version=1`. On this wire,
  `roster_version` means the Nakama session authorization epoch; it is not
  Hepta's author-team roster version. A v1 replacement is operator-only and is
  deliberately limited to key rotation: exactly one durably disconnected slot
  keeps the same human subject, binding/Agent identity and role while changing
  both its Agent key id and public key. Every slot receives a fresh
  authorization id in the complete 3–5 member set for `current+1`.
  Partial/mixed epochs and human/Agent substitution are rejected.
- Replacement invalidates every old admission, readiness, action cursor, and
  release acknowledgement. The session remains paused until every player rejoins with
  the fresh authorization and every Agent signs `participant.ready` against the
  new `roster_root`. Old sockets cannot act under their old metadata.
- Ordinary actions are accepted only in all-joined, all-connected, all-ready
  state. They are bound to the current authorization and server clock, ordered
  by a per-participant sequence plus global `expected_session_version`, and
  idempotent by signed fingerprint.
- `expires_at_unix` is the first-admission/epoch-consumption window. A member
  that has not durably joined before it expires cannot enter. Once joined, its
  signed actions and reconnects remain valid after that deadline until explicit
  epoch rotation, completion, or abandonment; actions must still not predate
  `issued_at_unix` or claim a future issue time. Session duration/idle policy is
  a separate long-lived control-plane concern and is not overloaded onto this
  short-lived admission credential.
- Completion has no winner. It requires a substantive action in the current
  epoch and a `paper.release.acknowledged` action from every current participant Agent,
  proving only that each Agent observed the same release hash. Human authorship
  approval is exclusively a Hepta `AuthorshipConsent` fact and is never inferred
  from a Nakama Agent action. Every acknowledgement references the exact
  terminal `paper_release_candidate_hash`.
- Every mutation is durably snapshotted before broadcast. Runtime restart
  durably fences formerly connected presences as disconnected. Clients resume,
  reconnect, and fetch missing events with an exclusive archive cursor.
- Join-attempt validation is non-mutating and expires after 30 seconds; durable
  admission occurs only in the actual Nakama `MatchJoin` callback. A roster
  replacement is persisted and kept in the archive, but is not broadcast to
  the now-stale roster: old-epoch presences are kicked and excluded from every
  subsequent recipient list even if a transport-level kick reports failure.
- All digest JSON values are `sha256:` plus 64 lowercase hex characters. Byte
  arrays use canonical padded base64. Unknown JSON fields are rejected.

## RPC and realtime surface

| RPC id | Purpose |
| --- | --- |
| `trnm_research_session_create_v1` | Operator creates a session from 3–5 authorizations. |
| `trnm_research_session_resume_v1` | Operator fences/starts a new runtime generation. |
| `trnm_research_session_evidence_v1` | Operator or bound current participant reads completion. |
| `trnm_research_session_archive_v1` | Operator or bound current participant pages durable events. |
| `trnm_research_session_complete_v1` | Operator requests cooperative completion. |
| `trnm_research_session_replace_roster_v1` | Operator installs a complete fresh roster epoch. |

The Nakama match name is `trnm_research_session_v1`. Join metadata contains
only `authorization_id`. Reliable opcodes are 11 (client signed action), 12
(durable event), 13 (ephemeral sender-only rejection), and 14 (signed
completion). Realtime delivery is not an acknowledgement; archive catch-up is
the recovery mechanism.

## Canonical primitives

A canonical frame begins with the ASCII domain and one NUL byte. A string is
UTF-8 bytes. Strings and byte arrays are `u32_be(length) || bytes`; `u32` and
`u64` are unsigned big-endian; `i64` is the big-endian two's-complement bit
pattern. A digest field is the raw 32-byte SHA-256 value without its JSON
prefix. JSON member order is never canonical.

Identifiers are non-empty valid UTF-8, contain no NUL, and are at most 512
Unicode code points. `session_id` additionally matches
`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`. Payloads contain 1–65,536 decoded bytes.

### Authorization and roster

`trnm_research_session_authorization_claim_v1\0` appends:

`schema, authorization_id, session_id, team_id, paper_project_id,
challenge_id, agent_id, agent_did, agent_key_id, agent_public_key(bytes32),
subject_user_id, participant_slot(u32), role, roster_version(u64),
roster_root(digest), ruleset_hash(digest), challenge_snapshot_hash(digest),
issued_at_unix(i64), expires_at_unix(i64)`.

The Ed25519 signed message is
`trnm_research_session_authorization_signature_v1\0`, then
`issuer_key_id(string), claim_frame(bytes)`.

The roster frame is `trnm_research_session_roster_v1\0`, then
`session_id, team_id, paper_project_id, roster_version(u64), member_count(u32)`,
then each entry in ascending slot order:

`participant_slot(u32), authorization_id, subject_user_id, agent_id,
agent_did, agent_key_id, agent_key_hash(digest), role`.

`agent_key_hash=SHA256(raw Ed25519 public key)` and
`roster_root=SHA256(roster_frame)`.

### Signed Agent action

`trnm_research_session_action_signature_v1\0` appends:

`schema, action_id, authorization_id, session_id, team_id, paper_project_id,
challenge_id, roster_version(u64), participant_slot(u32),
participant_sequence(u64), expected_session_version(u64), issued_at_unix(i64),
action_type, payload_type, payload(bytes), payload_hash(digest),
reference_hash(digest), agent_key_id`.

The signature itself is excluded. An action fingerprint is SHA-256 of
`trnm_research_session_action_fingerprint_v1\0`, then
`action_signing_frame(bytes), signature(bytes)`.

The exact v1 semantic whitelist is:

| action_type | payload_type | reference_hash |
| --- | --- | --- |
| `participant.ready` | `trnm.research-session.ready.v1` | current roster root |
| `research.task.claimed` | `trnm.paper-raid.task-claim.v1` | work-item hash |
| `agent.proposal.submitted` | `trnm.paper-raid.agent-proposal.v1` | proposal/parent hash |
| `artifact.manifest.published` | `trnm.paper-raid.artifact-manifest.v1` | artifact manifest hash |
| `review.submitted` | `trnm.paper-raid.review.v1` | reviewed revision/claim hash |
| `checkpoint.recorded` | `trnm.paper-raid.checkpoint.v1` | checkpoint manifest hash |
| `paper.release.acknowledged` | `trnm.paper-raid.release-acknowledgement.v1` | release candidate hash observed by the Agent; never human consent |

### Events, roots, and archive

Event facts use `trnm_research_session_event_v1\0`, then:

`schema, event_id, event_type, session_id, team_id, paper_project_id,
challenge_id, roster_version(u64), sequence(u64), causation_id,
occurred_at_unix(i64), participant_slot(u32), session_version(u64),
action_type, payload_type, payload(bytes), payload_hash(digest),
reference_hash(digest)`.

`event_hash=SHA256(event_facts_frame)` and:

```text
event_id = SHA256("trnm_research_session_event_id_v1\0" ||
                  bytes(session_id) || bytes(causation_id) || u64(sequence))
leaf = SHA256("trnm_research_session_event_leaf_v1\0" ||
              u64(sequence) || raw_event_hash)
node = SHA256("trnm_research_session_merkle_node_v1\0" || left || right)
```

Events are gapless from 1 and `session_version=sequence+1`. Odd Merkle levels
duplicate their final node. The archive frame is
`trnm_research_session_event_archive_v1\0`, then `event_count(u64)`, then each
`event_facts_frame(bytes), raw_event_hash(bytes)`. `archive_hash` is SHA-256 of
the whole frame.

Server event payloads also use canonical frames:
`trnm_research_session_participant_{joined,disconnected,reconnected}_v1` and
`trnm_research_session_roster_replaced_v1`, as frozen by the golden fixture.

### Cooperative completion

Terminal facts are `{result_code, paper_bundle_hash,
paper_release_candidate_hash, contribution_ledger_hash}` framed under
`trnm_research_session_terminal_facts_v1\0` in that order (string then three
digests). Derived roots are deliberately absent from the terminal event.

```text
commitment_id = SHA256("trnm_research_session_commitment_id_v1\0" ||
                       bytes(session_id) || event_root || archive_hash)
```

The completion JSON object fields are `schema, commitment_id, session_id,
team_id, paper_project_id, challenge_id, roster_version, roster_root,
terminal_facts, event_count, event_root, archive_hash, ruleset_hash,
challenge_snapshot_hash, completed_at_unix, authority_key_id, signature`.
The Ed25519 message uses
`trnm_research_session_completed_signature_v1\0` and the same order, except
terminal facts are their length-prefixed canonical frame and signature is
excluded.

The evidence response's bundled authority public key is diagnostic only. A
verifier must resolve `authority_key_id` through an externally pinned registry,
require the resolved key to equal the bundled key, and then verify the
completion signature.

## Durable Hepta callback receipts

Nakama first commits each authorization-epoch consumption request or completion
request, including its exact JSON bytes, SHA-256, and idempotency key, in the
same OCC write as the local state transition. Retries reuse those bytes. The
HTTP client never follows redirects while carrying `x-hepta-nakama-token`, and
accepts only the expected status with exactly `Content-Type: application/json`.
An unsigned 2xx response is not delivery.

The signed authorization consumption ACK is
`hepta.paper_raid.authorization_set_consumption_receipt.v1`. Its frame domain
is `hepta_research_session_authorization_set_consumption_receipt_v1\0`, then:

`schema, session_id, team_id, paper_project_id, challenge_id,
session_roster_version(u64), roster_root(digest), authorization_count(u32),
each ordered authorization_id, consumed_at_unix(i64), issuer_key_id`.

`session_roster_version` is the same Nakama authorization epoch called
`roster_version` on session actions/completion. The differently named ACK field
makes it explicit that this is not Hepta's author-team roster version.

The signed completion ACK is
`hepta.paper_raid.nakama_completion_receipt.v1`. Its frame domain is
`hepta_nakama_research_session_completion_receipt_v1\0`, then:

`schema, commitment_id(digest), session_id, team_id, paper_project_id,
challenge_id, roster_version(u64), roster_root(digest), event_count(u64),
event_root(digest), archive_hash(digest), ruleset_hash(digest),
challenge_snapshot_hash(digest), nakama_authority_key_id,
terminal_facts_frame(bytes), verified_at_unix(i64), issuer_key_id`.

Both signatures are canonical padded-base64 Ed25519 signatures. Nakama resolves
`issuer_key_id` only through its pinned Hepta issuer map; the response never
supplies trust. Exact signed receipt bytes and SHA-256 are persisted with the
delivered marker and reverified on every restart. A completed runtime remains
alive solely to retry pending callbacks (including beyond its ordinary runtime
generation lifetime) and terminates only after the signed completion ACK is
durable. After SIGKILL, resume or an evidence read starts a delivery-only
runtime for any completed snapshot with pending ACKs.
