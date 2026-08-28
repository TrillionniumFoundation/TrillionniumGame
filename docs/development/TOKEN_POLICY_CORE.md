# Rust session token policy core

Status: **source-level implementation candidate**.

The core defines token claims, key role/status/epoch selection, time and size validation, legacy compatibility boundaries, and post-signature claim acceptance. It does not implement HMAC, JWT serialization, key storage, KMS access, token parsing, or session persistence.

## Profiles

`NakamaV340Legacy` represents the pinned public claims `tid`, `uid`, `usn`, `vrs`, `exp`, and `iat`, HS256, separate access/refresh keys, and no token-carried key epoch. Overlapping verify keys are therefore ambiguous; this implementation fails closed rather than guessing.

`TrillionniumFamilyV1` requires a session family ID, family generation, and explicit key epoch. It is a versioned extension, not an unannounced Nakama compatibility claim.

## Crypto boundary

The pure core receives only a digest reference for key material and produces signing or verification plans. A later audited adapter must perform exact serialization and cryptography using a reviewed library or KMS. Raw key bytes must not enter logs, evidence, database rows, or these pure policy types.

## Rotation

Only `Active` keys issue. `Active` and `VerifyOnly` keys may verify when their time windows are valid. `Retired` keys reject. Family-v1 tokens identify the epoch; legacy tokens require exactly one eligible verification key.

## Remaining work

- exact JWT JSON/base64/signature adapter;
- independent HS256 library/KMS selection;
- legacy immutable Nakama token differential;
- refresh-family persistence and revocation integration;
- access/refresh key rotation and emergency revoke runbooks;
- socket revocation fanout;
- algorithm-confusion, malformed-token and timing/fuzz tests.

No signature compatibility, C1, production readiness, or public-online claim is made.
