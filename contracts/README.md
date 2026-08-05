# Contracts

`trnm_nakama_authoritative_match_v1` is the independent wire boundary for the
first authoritative two-participant match slice. The normative framing and
security rules are in [AUTHORITATIVE_MATCH_V1.md](AUTHORITATIVE_MATCH_V1.md),
machine-readable JSON Schemas are under `v1/`, and fixed hash cases are in
`golden-vectors.json`.

Consumers copy or publish a versioned artifact from this directory. They must
not import this repository, or any other sibling working tree, by filesystem
path. JSON Schemas describe the transport representation; canonical bytes used
for signatures and roots are the binary frames in the normative document, not
ordinary JSON serialization.

## Machine-readable surface

Every file in `v1/` is a self-contained JSON Schema Draft 2020-12 document.
References are local JSON Pointers, so validators do not need network access or
another schema file to validate one message. Unknown object fields are rejected
at every public object boundary.

Evidence responses include `authority_public_key_base64` for portable
verification and diagnostics. That bundled key is not a trust anchor.
Consumers must resolve `completion.authority_key_id` through their own pinned,
versioned authority registry or deployment configuration, require the two
public keys to match, and then verify the signature. Accepting only the key
carried in the same response proves self-consistency, not Nakama authority.

The admission, realtime, and durable evidence objects are:

- `signed-match-authorization.schema.json`
- `join-metadata.schema.json`
- `agent-command.schema.json`
- `command-rejected.schema.json` (ephemeral, sender-only)
- `match-event.schema.json`
- `terminal-facts.schema.json`
- `match-completed.schema.json`

The RPC wire schemas are:

- `create-match-{request,response}.schema.json`
- `resume-match-{request,response}.schema.json`
- `complete-match-{request,response}.schema.json`
- `evidence-{request,response}.schema.json`
- `archive-{request,response}.schema.json`
- `match-runtime-response.schema.json`
- `health-response.schema.json`
- `readiness-response.schema.json`

Run `bash scripts/check-nakama-contract.sh` to verify the exact published
schema set, Draft 2020-12 metaschemas, local-only references, strict object
boundaries, shared identifier/digest rules, and the complete independently
recomputed cross-language golden fixture.

`golden-vectors.json` deliberately contains deterministic Ed25519 seeds so
another language can reproduce every signature byte. They are test fixtures,
not credentials, and must never be copied into any deployed environment.
`scripts/verify-nakama-golden.mjs` uses only Node.js built-ins and recomputes
the public keys, frames, hashes, Merkle root, and signatures from those inputs.
