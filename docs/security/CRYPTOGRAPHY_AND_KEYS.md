# Cryptography and key lifecycle

Status: binding security design contract. It does not approve the current JWT adapter or grant token/session compatibility.

## 1. Scope

This contract covers:

- access and refresh JWT signing/verification;
- console, runtime HTTP, socket server and authority keys;
- provider callback verification;
- encryption/signing keys used by migration and evidence fixtures;
- KMS/HSM or secret-manager integration;
- key epochs, rotation, emergency revoke and retirement;
- in-memory, logging, diagnostics and artifact handling.

## 2. Primitive policy

Production and compatibility code uses reviewed cryptographic libraries or an independently reviewed, narrowly scoped compatibility implementation. New private implementations of hash, MAC, signature, cipher, key derivation, random generation or constant-time primitives require:

- an ADR explaining why an established library cannot satisfy the exact contract;
- standards vectors and adversarial corpus;
- constant-time and side-channel review where relevant;
- fuzzing and cross-implementation differential;
- named security owner and independent reviewer;
- a removal/review date.

Passing known-answer tests is necessary but not sufficient for approving a private implementation.

## 3. Current JWT adapter boundary

`crates/trnm-token-jwt-adapter` is a compatibility candidate. Until independent security acceptance:

- it earns no C2/C4/C5 or production credit;
- it must be compiled, tested and linted by the aggregate merge gate even if it remains a standalone Cargo workspace;
- unknown/malformed epoch routes must never fall back to the legacy key;
- unverified payload parsing must not be exposed as an authentication API;
- signature length must be exactly 32 bytes for HS256 before claims are trusted;
- algorithm confusion, duplicate JSON fields, malformed NumericDate, oversized input and invalid base64url must fail closed;
- the legacy route can be disabled independently.

The confirmed length-truncation defect in the local constant-time helper is tracked as `GAP-P1-CRYPTO-002`. Fixing that defect does not by itself approve the adapter.

## 4. Key domains

The following domains never share key bytes:

| Domain | Purpose | Required separation |
| --- | --- | --- |
| access token | short-lived API/socket authentication | separate from refresh and all server/operator keys |
| refresh token | refresh-family rotation | separate key/provider and audit trail |
| console session | operator authentication | separate tenant/audience and rotation |
| runtime HTTP | server-to-runtime request authentication | no token signing reuse |
| socket server | protocol server key compatibility | no console/runtime reuse |
| authority | signed game/match completion and authority receipts | asymmetric key preferred; no session reuse |
| evidence/fixture | non-production deterministic test signing | test-only root, never accepted in production |
| provider callbacks | Apple/Google/etc. callback verification | provider-specific trust and key cache |

A configuration validator rejects identical key identifiers or bytes across forbidden domains where the provider allows comparison.

## 5. Key provider interface

Application code receives opaque key handles, not environment strings containing raw bytes. A provider supports:

```text
resolve(domain, key_id, epoch) -> signing/verifying handle
active_epoch(domain) -> epoch
list_verification_epochs(domain) -> bounded set
revoke(domain, epoch)
health(domain)
```

Handles expose only the operations required by the algorithm. Debug output includes domain, provider and key ID/epoch but never secret material. Provider calls have deadlines, caching rules, metrics and fail-closed behavior.

Local development may use file/environment providers only under explicit non-production profiles. Production profiles require an approved secret manager/KMS/HSM boundary and rotation audit.

## 6. Token routing and claims

Legacy HS256 tokens without `kid` and epoch tokens are separate routes. Rules:

- `alg` is required and exactly matches the configured profile;
- `crit` and detached-payload behavior are rejected unless explicitly implemented and denominated;
- malformed/unknown `kid` never falls back;
- epoch header and payload claim must agree where the profile requires both;
- issuer and audience are exact profile contracts;
- expiration, issued-at, not-before, lifetime and clock-skew behavior are bounded and differentially tested;
- subject, username, variables and token IDs have size/count/type limits;
- access and refresh claims use distinct profiles and audiences;
- raw access/refresh tokens are never stored; only approved digests/identities are persisted.

## 7. Refresh-family replay and revocation

Refresh rotation is one transactional state machine:

```text
active token
 -> consumed and replaced
 -> replay of consumed token revokes the family
 -> all access/socket sessions for the family are disconnected
```

Requirements:

- one authority owns a refresh family during migration;
- consumed-token replay is atomic with family revocation;
- concurrent refresh requests cannot both succeed;
- revocation fanout is durable/retryable;
- emergency global/domain/user/session-family revocation has a runbook;
- rotation and revoke evidence includes database, API and socket effects.

## 8. Rotation lifecycle

```text
created
 -> verification-only warmup
 -> active signing
 -> verification-only grace
 -> revoked/retired
 -> destroyed according to retention policy
```

A rotation plan defines:

- epoch/key ID and provider location;
- activation and grace timestamps;
- maximum token lifetime and propagation delay;
- rollback behavior before and after signing activation;
- cache invalidation and node convergence;
- monitoring and abort thresholds;
- proof that old keys no longer sign;
- destruction approval and evidence retention.

Key rotation does not require accepting tokens beyond their configured lifetime. Unknown future epochs fail closed.

## 9. Emergency revoke

Triggers include secret exposure, unauthorized signing, provider compromise, algorithm defect and operator misuse. The runbook must support:

1. stop signing with the affected key;
2. distribute revocation before or with traffic fencing;
3. invalidate affected sessions/families;
4. disconnect sockets and stop authority actions;
5. rotate dependent keys without cross-domain reuse;
6. identify signed artifacts and time windows;
7. preserve forensic evidence without retaining raw secrets;
8. communicate product/user impact;
9. independently review recovery before reopening traffic.

## 10. Memory and process handling

- Secret types redact `Debug` and errors.
- Buffers are minimized and zeroized using an approved mechanism where meaningful.
- Ordinary zero-fill is not claimed as guaranteed zeroization without compiler/runtime guarantees.
- Core dumps are disabled or protected in production profiles.
- Swap, crash dumps, tracing, panic reports and heap profiles are reviewed for secret exposure.
- Secrets do not cross process boundaries except through an approved provider protocol.
- Fork/exec and child-process environments are not used to distribute production secrets.

## 11. Randomness and time

Production randomness comes from the operating system or approved provider. Deterministic randomness is test/oracle-only and clearly namespaced. Token IDs, nonces and keys have domain-specific entropy requirements.

Time comes from an injected clock interface where deterministic testing is required. Production validation detects unsafe clock skew and reports readiness/operational alerts according to profile; it does not silently enlarge token lifetime.

## 12. Provider callbacks and asymmetric verification

Provider key sets are fetched through bounded, authenticated HTTPS with cache/expiry and key-ID handling. On unknown key ID, refresh behavior is rate-limited and bounded; the implementation never accepts an unverifiable callback because the provider is unavailable. Callback identities, transaction IDs, amounts/states and replay protection are durable contract fields and cannot be normalized away.

## 13. Logging and evidence

Never log or archive:

- raw token or authorization header;
- refresh token;
- signing/encryption key bytes;
- provider shared secret;
- full credential payload;
- raw receipt where policy classifies it as sensitive.

Allowed evidence uses irreversible digests, token route/epoch, stable error class, redacted claim shape and fixture-only public values. Secret scanning runs before artifacts are accepted.

## 14. Required test matrix

- NIST/RFC vectors for used primitives;
- cross-library issue/verify differential;
- wrong algorithm, none algorithm and key confusion;
- malformed segment counts/base64/JSON/UTF-8;
- duplicate keys and deep/wide objects;
- signature lengths including 0, 31, 32, 33 and large deltas such as 288 vs 32;
- legacy disabled/unknown epoch/no fallback;
- issuer/audience/time/lifetime boundaries;
- concurrent refresh/replay/revoke;
- key activation/grace/revoke/node convergence;
- socket disconnect and authority stop;
- fuzz corpus with persisted crash seeds;
- redaction and artifact secret scans.

## 15. Approval criteria

The crypto/token gap reaches independently reviewed only when:

- exact-head mandatory CI is non-empty and successful;
- the implementation choice is recorded in an ADR;
- all vectors/fuzz/replay/rotation tests pass;
- dependency/provenance/licensing are clean;
- the key-provider and emergency runbooks are rehearsed;
- no open critical/high security findings remain beyond policy;
- a reviewer independent of the implementation accepts the evidence.
