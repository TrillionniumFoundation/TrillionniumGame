# JWT provider adapter v1

Status: **source candidate; no production provider or compatibility credit**.

## Purpose

`crates/trnm-token-jwt-provider-adapter` moves JWT authentication onto the opaque provider boundary from ADR-0002. The adapter reuses the bounded base64url and JSON parser, but does not call the private SHA/HMAC implementation.

## Authentication order

```text
bound complete token
 -> require exactly three non-empty segments
 -> decode and parse bounded JOSE header
 -> require alg == HS256
 -> validate typ and strict known fields
 -> resolve Legacy or Epoch(kid) to an opaque key reference
 -> decode exact 32-byte signature
 -> provider verifies exact encoded header.payload
 -> only after Accepted: decode payload bytes
 -> caller parses and validates claims
```

The invalid-payload/rejected-signature tests enforce this order: an unauthenticated malformed payload returns signature rejection, while the same malformed payload produces a payload decode error only after a test provider accepts the signature.

## Key routing

- no `kid` may use the legacy route only when the profile explicitly permits it;
- epoch `kid` must use `trnm-kep-v1:<positive canonical decimal>`;
- epoch zero, leading zeroes, non-digits or another prefix are rejected;
- the resolver receives an explicit `KeyDomain` and route;
- a production resolver must reject unknown epochs and must never retry through the legacy key.

## Remaining work

The source candidate deliberately stops before:

- a reviewed RustCrypto or remote KMS/HSM provider;
- full Nakama claim mapping and numeric-date policy;
- exact legacy/epoch issue output;
- access/refresh profile separation at call sites;
- duplicate JSON key, malicious input and fuzz completion;
- key rotation, emergency revoke and refresh-family/socket propagation;
- independent security review and current-head evidence.

## Source gate

```bash
cargo fmt --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-jwt-provider-adapter/Cargo.toml --all-targets --locked -- -D warnings
```

## Claim boundary

This source candidate cannot close the parent crypto gap. It grants no token compatibility, session compatibility, C2, C4, SG5, SG8 or production readiness.
