# ADR-0002 — Reviewed JWT cryptographic primitives with a byte-exact compatibility layer

- Status: **accepted architecture direction; implementation and security acceptance pending**
- Date: 2026-08-30
- Affected gaps: `GAP-P0-CRYPTO-001`, `GAP-P1-CRYPTO-002`
- Affected parity: `TG-PAR-017`

## Context

The existing standalone JWT adapter intentionally avoids unverified decode and contains useful fail-closed route, bound and claim logic. It also contains private implementations of SHA-256, HMAC-SHA256, base64url, JSON parsing/encoding and constant-time equality. A confirmed length-difference truncation defect was corrected at source level, but one corrected bug does not establish confidence in a private security primitive suite.

Exact Nakama legacy token bytes and malformed-token behavior still require a narrow compatibility layer. This does not require retaining private cryptographic primitives.

## Decision

The production candidate must use reviewed Rust cryptographic primitives for:

- SHA-256;
- HMAC-SHA256;
- constant-time tag comparison;
- key zeroization where software-held key material is unavoidable.

Library selection must meet all of the following:

- Rust 1.85.1 compatibility for the approved release train;
- no unreviewed native code or FFI without a separate ADR and isolation plan;
- maintained security posture and published vulnerability process;
- locked checksums, license approval, SBOM and provenance;
- exact tag verification without an API that exposes unverified claims to callers;
- test vectors, fuzzability and deterministic compatibility adapter behavior;
- bounded allocations and explicit key material lifetime.

The byte-exact compatibility layer may retain custom logic only where required to reproduce the pinned public contract, such as strict legacy field mapping, canonical issuance profile, duplicate-field rejection policy and legacy/epoch routing. That layer must call the reviewed primitives and cannot implement its own MAC/hash/tag comparison.

## Candidate implementation shape

```text
TokenInputBounds
 -> strict segment parser
 -> bounded header parse for route selection
 -> reviewed HMAC tag verification and constant-time comparison
 -> bounded payload parse after successful authentication
 -> exact claim/profile validation
 -> VerifiedToken
```

Unknown or malformed `kid` never falls back to a legacy key. Access and refresh keys remain separate. Legacy tokens without `kid` are an explicit profile, not a generic fallback.

## Migration

1. freeze the existing adapter as a reference candidate;
2. generate a deterministic corpus covering valid and malicious tokens;
3. implement a reviewed-primitives adapter behind the same public verified-token contract;
4. run old/new/pinned-Nakama three-way differentials;
5. require byte equality for issuance where declared and behavior equality for verification/errors according to the profile;
6. fuzz both adapters with the same corpus and assert no acceptance expansion;
7. run refresh-family replay, key rotation, unknown epoch, emergency revoke and socket-disconnect integration;
8. independently review the selected crates, compatibility wrapper, key lifecycle and evidence;
9. remove production use of the private primitive module; retain only quarantined historical source if legally/operationally required.

## Rejected alternatives

### Permanently accept the private implementation after adding vectors

Rejected. Known-answer vectors and fuzzing reduce risk but do not substitute for mature primitive implementations and independent review.

### Use a general JWT library with unverified-decode-first application flow

Rejected. Route selection may parse only the bounded header required to select a key; claims cannot influence authority before exact tag verification.

### Change all tokens to a new algorithm immediately

Rejected as a compatibility migration strategy. New native profiles may be versioned separately, but the pinned legacy profile must be supported until its migration and revocation window is complete.

## Consequences

- the standalone adapter remains in the aggregate security-critical lane during migration;
- dependency and lockfile surface increases and must be governed;
- exact issuance may require a compatibility encoder independent of a generic JWT serializer;
- existing source tests remain useful as a differential oracle but grant no production credit;
- `GAP-P0-CRYPTO-001` remains open until replacement or explicit independently reviewed exception evidence is accepted.

## Acceptance evidence

- selected dependency identities, checksums, licenses, SBOM and advisories;
- NIST/RFC/Wycheproof-class vectors where applicable;
- malformed and algorithm-confusion corpus;
- old/new/Nakama differential artifact;
- fuzz artifact and coverage summary;
- key rotation/revoke/replay/socket integration;
- no secret leakage in logs, panic, core/evidence or Debug output;
- independent cryptography and application-security review.

This ADR is an architecture decision, not security-review evidence and not a compatibility or production claim.