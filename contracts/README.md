# Contracts

Versioned schemas shared with World, Hepta, Chain adapters and Integration live
here. Consumers copy or publish immutable artifacts; they must not import this
repository, or any sibling working tree, by filesystem path.

## Authoritative match v1

`trnm_nakama_authoritative_match_v1` is the independent wire boundary for the
first authoritative two-participant match slice. Normative framing and security
rules are in [AUTHORITATIVE_MATCH_V1.md](AUTHORITATIVE_MATCH_V1.md), public JSON
Schemas are under `v1/`, and fixed cross-language hash/signature cases are in
`golden-vectors.json`.

JSON Schemas describe transport. Canonical bytes used for signatures and roots
are the binary frames in the normative document, never ordinary JSON field
order. Every public object rejects unknown fields. Evidence includes an
authority public key for diagnostics, but consumers must resolve
`authority_key_id` through their own pinned registry and require an exact key
match before verifying `MatchCompletedV1`.

The admission, realtime and durable evidence objects include:

- `signed-match-authorization.schema.json`;
- `join-metadata.schema.json`;
- `agent-command.schema.json`;
- `command-rejected.schema.json`;
- `match-event.schema.json`;
- `terminal-facts.schema.json`;
- `match-completed.schema.json`.

The RPC surface includes create, resume, complete, evidence, archive, runtime,
health and readiness schemas under `v1/`.

Run `bash scripts/check-nakama-contract.sh` to verify the exact schema set,
local-only references, strict object boundaries and the independently
recomputed golden fixture. Deterministic private seeds in `golden-vectors.json`
are test-only and must never be deployed.

## World transition v1

- `world-transition-v1.schema.json` is a byte-exact vendored World schema.
- `world-transition-v1-consumer-lock.json` pins the exact World commit, tree and
  source blob identities.
- `world-transition-v1-adapter-status.json` records Nakama delivery state
  without granting cross-repository, cutover or release credit.

The transition boundary accepts unsigned deterministic game-domain material
only. It cannot carry participant admission, canonical global sequence,
idempotency authority, archive roots, completion signatures, Chain finality or
wallet mutation.

Integration, not this repository, owns the final exact multi-repository
component lock and representative E2E evidence.
