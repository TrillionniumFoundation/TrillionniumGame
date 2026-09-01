# Security and privacy

Status: **authoritative current documentation**  
Revision: 2026-09-01

This document is the current engineering security contract. Vulnerability reporting and disclosure instructions remain in [`../SECURITY.md`](../SECURITY.md).

## 1. Security boundary

The program covers token signing and verification, refresh-family state, Console/operator identity, runtime and socket credentials, authority receipts, provider callbacks, database transport, secrets, evidence fixtures, dependency provenance, privacy and incident response.

Security-sensitive source remains a candidate until exact-head tests and the required independent review are accepted. Passing known-answer vectors is necessary but not sufficient.

## 2. Primitive policy

Production and compatibility code uses reviewed ecosystem cryptographic libraries or an independently reviewed narrow compatibility implementation. A private hash, MAC, signature, cipher, KDF, random or constant-time primitive requires:

- a documented necessity and removal/review date;
- standards and adversarial vectors;
- side-channel review where relevant;
- fuzz and cross-implementation differential;
- dependency/provenance/license review;
- a named security owner and independent reviewer.

Existing hand-written helpers are compatibility candidates, not automatically approved primitives. The aggregate gate must always compile, test and strictly lint security-critical workspaces.

## 3. JWT and token adapter

The current JWT adapter is not production-approved. Required behavior includes:

- exact allowed algorithm, never `none` or algorithm confusion;
- strict segment, base64url, JSON, UTF-8 and size validation;
- duplicate-field rejection;
- exact HS256 signature length before claims are trusted;
- full-width unequal-length rejection, including a 256-byte delta;
- strict legacy and epoch routes;
- unknown or malformed key ID/epoch never falls back;
- exact issuer, audience, time, lifetime and skew policy;
- bounded subject, username, variables and token IDs;
- no public API that treats unverified payload parsing as authentication.

The provider-facing adapter and compatibility format adapter remain separate boundaries so reviewed key operations can replace private primitives without changing public token semantics.

## 4. Key domains

Key bytes and provider identities are separated across:

| Domain | Purpose |
| --- | --- |
| access | short-lived API and socket authentication |
| refresh | refresh-family rotation/replay protection |
| console | operator sessions and administration |
| runtime | server-to-runtime authentication |
| socket server | compatibility server key behavior |
| authority | signed match/game completion and authority receipts |
| provider callback | provider-specific verification trust |
| evidence fixture | deterministic non-production tests only |

Configuration rejects forbidden domain reuse when comparable identifiers or bytes are available. Test roots and deterministic keys never enter production profiles.

## 5. Key provider interface

Application code receives opaque handles, not raw environment strings. The provider contract supports resolving a domain/key/epoch, selecting the active epoch, bounded verification epochs, revoke and health. Handles expose only required operations.

Production profiles require an approved secret manager, KMS or HSM boundary. Local file/environment providers are explicit development profiles. Provider calls have deadlines, cache/refresh rules, metrics and fail-closed behavior.

## 6. Rotation lifecycle

```text
created
 -> verification-only warmup
 -> active signing
 -> verification-only grace
 -> revoked or retired
 -> destroyed under retention policy
```

Every rotation records key ID/epoch, provider location, activation/grace windows, maximum token lifetime, propagation budget, rollback boundary, cache invalidation, node convergence, monitoring/abort thresholds, proof old keys no longer sign and destruction approval.

Unknown future epochs fail closed. Rotation does not silently extend token lifetime.

## 7. Refresh-family replay and revocation

```text
active refresh token
 -> consumed and replaced atomically
 -> replay of consumed token revokes the family
 -> access and socket sessions for the family are disconnected
```

Concurrent refresh requests cannot both succeed. Replay revocation, logout and emergency revoke are durable and auditable. During migration, every family has exactly one writer. Generic public failures must not reveal whether a user, family or token exists.

## 8. Administration plane

The final administration plane is separate from public game traffic. It requires internal or loopback bind by default, service identity/mTLS, RBAC, MFA where applicable, audit records and approval for dangerous actions. A single static bearer token is only a bounded source-candidate mechanism and is not the production design.

Drain, migrate, key rotation, rollback, data export and destructive Console actions are authenticated, authorized and audited. Metrics endpoints expose low-cardinality non-sensitive data only.

## 9. Database and transport security

Production database transport uses verify-full TLS with hostname and chain verification. Client identity is a paired certificate and PKCS#8 key. There is no invalid-certificate or invalid-hostname bypass. Plaintext mode is explicit and limited to loopback CI/local evidence.

Required evidence includes invalid/expired chain, wrong hostname, client identity failure, rotation/reload, pool churn, cancellation, statement/lock timeout and failover for both database profiles.

Public HTTP/gRPC/WebSocket endpoints require mature TLS, framing and parser boundaries before production. Hand-written parsers remain fuzz/differential targets and may not gain production approval solely from unit tests.

## 10. Runtime isolation

Runtime modules receive capability-limited interfaces with deadline, cancellation, memory/fuel/CPU/output budgets and controlled clock/random/provider inputs. No raw secrets, unrestricted filesystem or unrestricted network access is exposed. Engine/profile differences receive independent security conclusions.

A panic, trap, timeout or resource exhaustion maps to a stable bounded failure and cannot corrupt the server supervisor or leak another project/session context.

## 11. Secret and process handling

- Secret-bearing types redact `Debug`, logs and errors.
- Raw tokens, authorization headers, refresh credentials, signing keys and provider secrets are never logged or retained in evidence.
- Buffers are minimized and zeroized through an approved mechanism where meaningful; ordinary zero-fill is not overclaimed.
- Core dumps, swap, crash reports, traces and heap profiles are reviewed or disabled/protected in production.
- Child-process environments are not a production secret distribution channel.
- Error responses never expose database URLs, SQL, key paths, token state or private reasons.

## 12. Privacy

Data collection follows purpose limitation and least retention. User identifiers, tokens, payloads, receipts and provider transaction data are not metric labels. Logs and traces use stable event IDs, redaction classes and correlation digests.

Test fixtures use synthetic or approved anonymized data. Evidence manifests state sensitivity, location, retention and deletion policy. Export/deletion workflows must cover primary storage, indexes, caches, backups and evidence where legally required, while preserving narrowly required security/audit records.

## 13. Supply chain

Required controls include immutable Action SHAs where external actions are permitted, immutable container digests, Cargo/Go lock integrity, advisory scanning, license policy, SBOM, provenance, secret scanning and release signature verification. A string that resembles a pinned action is not accepted until the object/publisher identity is verified by policy.

Workflow changes require supply-chain/program-governance review. Repository write permissions are denied by default and granted only to narrowly audited administrative workflows.

## 14. Security test matrix

- RFC/NIST vectors and cross-library issue/verify differential;
- malformed segment/base64/JSON/UTF-8 and oversized inputs;
- wrong algorithm, duplicate fields and key confusion;
- signature lengths 0, 31, 32, 33 and large deltas;
- legacy disabled, unknown epoch and no-fallback behavior;
- issuer/audience/time/lifetime/skew boundaries;
- concurrent refresh/replay/revoke and socket disconnect;
- key activation/grace/revoke/node convergence;
- TLS chain/hostname/client identity/rotation failures;
- parser/token/runtime fuzz with persisted crash seeds;
- authorization/RBAC/MFA negative matrices;
- redaction and artifact secret scans;
- penetration test before public production approval.

## 15. Incident and emergency revoke

On exposure or signing/provider compromise: stop affected signing, fence traffic, distribute revocation, invalidate sessions/families, disconnect sockets, stop authority actions, rotate dependent domains without reuse, preserve redacted forensics, identify impacted artifacts/windows and obtain independent recovery review before reopening.

An emergency repository bypass may contain an incident but cannot grant compatibility, production or retirement authority.

## 16. Approval boundary

Security gaps close only when implementation choice, vectors/fuzz, key provider, rotation/revoke, dependencies, exact-head artifacts and independent security review are accepted. Current source candidates do not establish C2/C4/C5, production readiness or public-online approval.
