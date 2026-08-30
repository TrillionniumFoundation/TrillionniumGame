# ADR-0002 — JWT cryptographic provider boundary

- Status: proposed; blocks production token credit
- Date: 2026-08-30
- Owners: security, identity
- Gap: `GAP-P0-CRYPTO-001`

## Context

The compatibility adapter currently contains private SHA-256, HMAC-SHA256 and equality code. Focused standard vectors exist and the confirmed length-truncation defect has a source fix, but vector coverage does not substitute for mature implementation review, dependency provenance, side-channel analysis, key lifecycle or independent security approval.

The parser and Nakama claim mapping also have compatibility requirements that do not justify owning the cryptographic primitive.

## Decision

Introduce an explicit HS256 provider boundary and separate three responsibilities:

```text
bounded JWT syntax and claim compatibility
cryptographic sign/verify provider
key source, epoch routing and lifecycle
```

The production default must use reviewed Rust cryptographic crates or an approved KMS/HSM-backed provider. The existing private primitive may remain temporarily only as a differential reference under a non-production feature. It must not be selected by a production build, migration release, canary or public deployment.

The expected software-provider family is:

- HMAC-SHA256 from the RustCrypto ecosystem;
- SHA-256 from the RustCrypto ecosystem;
- constant-time verification from the provider implementation or a reviewed constant-time primitive;
- zeroizing secret containers where they materially improve the supported platform boundary.

Exact crate versions, checksums, license records and MSRV are locked in the implementation PR after dependency/security review. This ADR does not authorize floating versions.

## Provider contract

```text
sign_hs256(key_handle, exact_signing_input) -> 32 bytes
verify_hs256(key_handle, exact_signing_input, signature) -> accepted/rejected
```

Requirements:

- exact encoded header and payload bytes are authenticated;
- verification never exposes an unverified claims API;
- signature length is checked before provider verification;
- access and refresh key domains are distinct;
- unknown `kid`/epoch never falls back to a legacy key;
- key bytes are not included in Debug, logs, metrics, errors or evidence;
- provider errors are mapped to stable internal classes without leaking key state;
- remote KMS latency/cancellation occurs outside mutable database transactions.

## Migration sequence

1. Freeze the current compatibility corpus and exact output vectors.
2. Add the provider interface without changing public token behavior.
3. Add reviewed software provider and optional remote-key provider.
4. Run both providers over valid, malformed and adversarial corpora.
5. Require byte-identical issue output where canonical output is a contract.
6. Require identical accept/reject and principal mapping for verification.
7. Execute legacy/epoch rotation, emergency revoke, refresh replay and socket-disconnect scenarios.
8. Obtain independent security review.
9. Disable the private provider in default and production features.
10. Remove it after the rollback window or retain it only in a non-shipping differential test crate.

## Required evidence

- RFC/NIST and cross-library vectors;
- algorithm-confusion and malformed-token corpus;
- duplicate JSON key and parsing-limit tests;
- signature length and 256-byte length-difference regression;
- fuzzing of all three segments and claims;
- rotation/revoke/refresh-family concurrency tests;
- dependency, license, SBOM and provenance report;
- supported-platform side-channel review;
- independent security decision with expiry.

## Rejected alternatives

### Keep private crypto because vectors pass

Rejected. Passing vectors does not establish complete correctness, side-channel behavior, lifecycle safety or independent review.

### Parse claims before signature verification

Rejected. Only the bounded header needed for provider/key routing may be parsed before authentication; claims are interpreted after successful verification.

### Fallback from unknown epoch to the legacy key

Rejected. It creates downgrade and ambiguous key-authority behavior.

### Treat KMS as automatically safer

Rejected. KMS changes availability, latency, cancellation, rate-limit and audit boundaries and must be tested as a separate provider profile.

## Consequences

- `GAP-P0-CRYPTO-001` remains open until implementation, exact-head execution and independent review.
- the confirmed equality defect may be `fixed-source`, but does not close the parent gap;
- production builds must eventually fail when no approved provider is configured;
- provider choice becomes an explicit compatibility/security profile rather than an implicit parser detail.
