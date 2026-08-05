# Hepta → Nakama signed research control v2

Status: frozen for Paper Raid v0.

This contract authorizes Hepta to create, resume, replace the roster of, or
complete a 3–5 participant Nakama research session. A v2 caller never sends a
Nakama operator token. Nakama verifies a short-lived Ed25519 command before it
creates a command record, session, runtime, outbox, or any other side effect.

The JSON wire schemas are in `research-control-v2/`. The deterministic Go and
Node interoperability fixture is `research-control-golden-vectors.json`.

## Trust boundary

- `TRNM_HEPTA_CONTROL_ISSUER_KEYS` is a JSON map from control issuer key ID to
  a raw 32-byte Ed25519 public key encoded as padded base64 (hex is also
  accepted by the Nakama configuration parser).
- A control public key MUST differ from every normal Hepta authorization issuer
  key and from Nakama's completion authority key. Duplicate public keys within
  the control trust set are rejected even when their key IDs differ.
- The claim audience is exactly `trnm:nakama:research-control:v2`.
- The claim lifetime is 1–120 seconds. Verification permits at most 30 seconds
  of clock skew before `issued_at_unix` or after `expires_at_unix`.
- `operation` and `target_rpc` are a fixed one-to-one mapping:

  | operation | target RPC |
  | --- | --- |
  | `create` | `trnm_research_session_create_v2` |
  | `resume` | `trnm_research_session_resume_v2` |
  | `replace_roster` | `trnm_research_session_replace_roster_v2` |
  | `complete` | `trnm_research_session_complete_v2` |

## Canonical frame grammar

Integers are big-endian. `u32`, `u64`, and `i64` occupy 4, 8, and 8 bytes.
`digest` is the raw 32-byte value of a canonical `sha256:<64 lowercase hex>`
string. A domain is its exact UTF-8 bytes followed by `0x00`. A `bytes` or
UTF-8 `string` field is encoded as `u32(byte_length) || bytes`.

`payload_hash` is **not a JSON digest**. It is SHA-256 over the exact
operation-specific, domain-separated business frame below. JSON member order,
whitespace, and escaping therefore cannot change the signed business meaning.

The normal v1 authorization signing frame is:

```text
authorization_signing_v1 =
  domain("trnm_research_session_authorization_signature_v1") ||
  string(issuer_key_id) ||
  bytes(authorization_claim_v1_frame)
```

Each v2 authorization envelope is:

```text
authorization_envelope_v2 =
  domain("trnm_research_control_authorization_envelope_v2") ||
  bytes(authorization_signing_v1) ||
  bytes(raw_64_byte_authorization_signature)
```

The four business frames are:

```text
create_business_v2 =
  domain("trnm_research_control_create_business_v2") ||
  string("trnm.nakama.research-session.create.v2") ||
  string(session_id derived from the ordered authorizations) ||
  string(authorization_set_id) ||
  u32(authorization_count) ||
  bytes(authorization_envelope_v2[slot 1]) || ...

resume_business_v2 =
  domain("trnm_research_control_resume_business_v2") ||
  string("trnm.nakama.research-session.resume.v2") ||
  string(logical_session_id) ||
  string(authorization_set_id)

replace_business_v2 =
  domain("trnm_research_control_replace_business_v2") ||
  string("trnm.nakama.research-session.replace-roster.v2") ||
  string(logical_session_id) ||
  string(new_authorization_set_id) ||
  u32(authorization_count) ||
  bytes(authorization_envelope_v2[slot 1]) || ...

complete_business_v2 =
  domain("trnm_research_control_complete_business_v2") ||
  string("trnm.nakama.research-session.complete.v2") ||
  string(logical_session_id) ||
  string(current_authorization_set_id) ||
  bytes(terminal_facts_v1_frame)
```

Create and replacement authorizations MUST be ordered by contiguous
`participant_slot` values starting at 1, contain 3–5 entries, and bind one
session and one roster version. Their signed v1 frames and signatures are part
of the v2 business frame; changing an authorization or its signature changes
`payload_hash`.

The control claim canonical frame is:

```text
control_claim_v2 =
  domain("trnm_research_control_claim_v2") ||
  string("trnm.nakama.research-control.claim.v2") ||
  string(command_id) || string(operation) || string(target_rpc) ||
  string(session_id) || u64(session_roster_version) ||
  string(authorization_set_id) || digest(payload_hash) ||
  string("trnm:nakama:research-control:v2") ||
  i64(issued_at_unix) || i64(expires_at_unix) || string(issuer_key_id)

control_signing_v2 =
  domain("trnm_research_control_signature_v2") || bytes(control_claim_v2)
```

`signature` is Ed25519 over `control_signing_v2` and is padded base64 on the
JSON wire.

## Epoch bindings

- `create`: the claim roster version is the authorization roster version
  (Paper Raid starts at 1), and the claim set ID is the created set ID.
- `resume`: the claim binds the current durable roster version and current set
  ID. It may recreate only the ephemeral runtime, never mutate the research
  snapshot.
- `replace_roster`: the claim roster version is exactly current version + 1;
  the claim set ID and request set ID identify the new authorization set.
- `complete`: the claim binds the current durable roster version and current
  authorization set ID.

## Durable idempotency and recovery

`command_id` is a lowercase UUID and is globally unique across all four
operations and sessions. Nakama canonicalizes the fully decoded strict JSON
request and stores those bytes, their checksum, the claim bindings, the
original acceptance time, and a `pending`/`applied` state. Those stored request
bytes are for exact replay and conflict detection only; they are not the
business payload frame.

- Replaying the same command and typed request returns the exact stored v2
  response bytes, including after the short-lived claim expires.
- Reusing a command ID with any different typed request is a conflict.
- Unknown JSON members are rejected, including `operator_token`.
- Create stores the new session snapshot and pending command in one atomic
  storage batch before creating a runtime.
- Resume stores its pending command before ensuring a runtime.
- Replacement and completion first reserve a pending command; the match loop
  then writes the updated snapshot, required outbox, and applied command result
  in one atomic batch.
- A process death in a pending create/resume window is recovered by an exact
  command retry. A pending replacement/completion whose runtime died first
  requires a separate, currently valid signed resume; current-epoch Agents
  must also reconnect before completion because process restart durably fences
  old socket presence. The original replacement/completion is then retried
  byte-for-byte. Its stored acceptance time proves that its signature was valid
  when accepted, so that original command does not need to be extended or
  re-signed.

The v2 response schema wraps the existing v1 runtime or evidence response with
the command ID, operation, and target RPC. Existing v1 RPC names, operator-token
behavior, schemas, and golden bytes remain unchanged.
