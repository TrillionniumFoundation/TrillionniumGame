# trnm-token-crypto-provider

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-crypto-provider`  
Workspace class: `root`
Lifecycle: `security-critical-crypto-provider`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-crypto-provider`. Its authority is limited to the module boundary described here: **opaque key-operation provider, deterministic lifecycle routing and authenticated signer-journal boundary**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, real provider integration and the independent reviews required by the linked gaps.

## Responsibilities

- define opaque sign/verify operations that never return key bytes;
- preserve six distinct key domains: access, refresh, console, runtime HTTP, socket and authority;
- schedule nonzero monotonic epochs with non-overlapping signing windows;
- select one exact active signing epoch and one exact requested verification epoch;
- bound active records, verification overlap and audit growth;
- revoke immediately and monotonically;
- retire and externally archive revoked or verification-expired key records while preserving the epoch/time high-watermarks;
- bind every external signing operation to an immutable request and one exact provider identity, endpoint and positive dispatch attempt;
- require a trusted `SignerOutcomeVerifier` to authenticate the complete live provider outcome, including receipt, actual returned signature bytes and retained digests, before terminal-state mutation;
- reserve every admitted signing operation's terminal archive slot and preserve provider-receipt uniqueness across checkpoint compaction;
- expose bounded handle-free lifecycle status and health.

Non-goals: this crate does not define JWT format, implement HS256, expose raw production keys, persist schedules, distribute rotation state between nodes, implement a production provider-outcome verifier, or substitute a real KMS/HSM deployment.

## Architecture and dependencies

JWT and token services call opaque operations through this boundary. Local deterministic providers are development-only profiles. Software key buffers use the pinned `zeroize` primitive when dropped so clearing is not represented by an ordinary optimizable fill; this remains a best-effort process-memory control and does not provide register clearing, memory locking, core-dump protection or HSM custody. `KeyEpochRegistry` stores routing metadata only: domain, opaque handle, epoch and time windows. Provider calls, durable persistence and cross-node convergence remain outside the registry.

Signer-journal trust is deliberately split:

1. `SigningRequest` binds journal epoch, operation identity, key domain/epoch and payload digest.
2. `SignerDispatchIdentity` binds that exact request to a provider identity, endpoint and attempt.
3. `SignerOutcomeEvidence` binds the same request and dispatch to the provider receipt, actual `Signature32`, signature digest and provider-evidence digest.
4. `SignerOutcomeVerifier` authenticates that complete live evidence before the journal may confirm the operation.
5. `SignerJournalArchiveVerifier` separately authenticates durable checkpoints/predecessors and answers historical receipt-absence queries.

A live outcome verifier cannot substitute for archive-chain verification, and an archive verifier cannot authenticate a new provider result. Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Unknown domain or epoch fails closed. No verification lookup falls back to another epoch or domain. Provider calls have an external deadline contract. Raw key bytes are never represented by this API. Revoke is monotonic and idempotent.

Key handles are redacted from Debug output. A handle may be inspected explicitly only by code already holding the `KeyHandle`; generic diagnostics for `KeyHandle`, `KeyReference`, lifecycle windows and registries do not print its value.

A signer operation cannot be dispatched with a request different from the prepared request. A dispatched or indeterminate operation cannot become `Confirmed` from caller-supplied digests alone. Reconciliation validates the typed evidence, requires the exact recorded dispatch and invokes `SignerOutcomeVerifier` before any record or receipt-owner mutation. Wrong request, key domain/epoch, payload, provider, endpoint, attempt, receipt, signature bytes, signature digest or provider-evidence digest fails without mutation. Exact confirmed replay remains idempotent only after fresh verifier acceptance.

Signer checkpoints are locally shape-locked: sequence one has no predecessor; every later sequence requires a nonzero predecessor digest; sequence equals the retired journal epoch; and the active epoch is exactly the retired epoch plus one. The external verifier must authenticate the real predecessor chain rather than treating local nonzero checks as proof.

Public Rust types, serialized fields, configuration keys, database predicates, provider evidence formats and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Lifecycle invariants

- epochs are greater than zero and strictly increase per domain, including after retirement;
- signing windows use half-open intervals and never overlap inside one domain;
- Verification epochs are bounded to two simultaneous windows per domain;
- verification always names an exact epoch and does not search for an alternative;
- an emergency revoke blocks signing and verification immediately;
- retirement is permitted only after revocation or verification expiry;
- every lifecycle mutation carries an exact expected revision and non-regressing mutation time;
- stale revision, clock regression, capacity exhaustion, audit exhaustion and revision overflow fail before state mutation;
- configured records are limited to eight per domain and audit events to 256;
- every admitted signer operation reserves a terminal/archive slot;
- every dispatched operation retains one exact provider/endpoint/attempt identity;
- every confirmed operation has one authenticated provider receipt owner;
- signer-journal rollover cannot orphan or silently restart the checkpoint chain;
- status and health expose counts and epoch numbers, never handles, tokens or credentials.

The operational registry remains deterministic and bounded. Terminal operational records leave memory only through a digest-chained `KeyEpochArchiveCheckpoint` accepted by a trusted `KeyEpochArchiveVerifier`. The checkpoint carries the global highest epoch, last lifecycle time, authority-loss state, retained verification window and exact archived records. Local shape validation requires exactly one retained `Active` record matching the highest epoch when signing authority exists, and no retained `Active` record after authority loss. Verify-only retirement must follow activation; revoked and retired timestamps cannot precede activation or exceed the lifecycle high-water. These structural checks execute before the external verifier. New key IDs after archival require an external absence proof, and archived verification requests return an explicit durable-archive requirement instead of falling back. A durable adapter must atomically persist and verify this checkpoint before the window is restored on another node.

`SignerJournal` uses the same durable handoff principle. Every request and asynchronous handle binds an explicit `SignerJournalEpoch`; admission checks both active capacity and the total epoch tombstone budget, so every accepted operation can be archived. Confirmed/rejected records become bounded tombstones. Epoch advance requires zero active records and a trusted, digest-chained `SignerJournalCheckpoint` containing every tombstone and receipt binding. The current receipt map is then compacted, while subsequent reconciliation must obtain a durable absence proof before accepting a receipt not present locally. Operation IDs may be reused only in a later epoch; stale handles and old-epoch requests fail before mutation.

The in-memory registries remain deterministic source components, not production lifecycle databases. A durable adapter must atomically store revision, high-watermarks, windows, revocations, retirements, authenticated signer outcomes, checkpoint chains and audit receipts before this boundary can be shared by multiple nodes.

## Correctness and failure model

Active/verification epoch transitions are deterministic, bounded and revision-fenced. Two writers starting from the same revision cannot both apply through a conforming durable adapter. Unknown epochs, stale snapshots, out-of-order clocks and exhausted counters fail closed.

Signer reconciliation follows validate → exact local request/dispatch binding → trusted outcome verification → archive receipt check when applicable → terminal mutation. A failed verifier, mismatched evidence, reused current receipt, archived receipt, malformed checkpoint or invalid predecessor shape leaves the operation and receipt index unchanged. Transport loss changes a dispatched operation to `Indeterminate` and forbids redispatch; recovery requires an authenticated exact outcome or a separately specified rejection/reconciliation decision.

Archive/checkpoint construction accepts only terminal records already admitted through the trusted live boundary. Restoring a checkpoint requires both local shape validation and external authentication. A successor sequence without a predecessor, a first sequence with a predecessor, or a sequence/epoch mismatch is rejected before journal construction.

All inputs, loops, retries, batches, queues, allocations, archive records and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, partial-failure and cross-node convergence behavior must be represented in deterministic and live tests where applicable.

## Security and privacy

Production requires a reviewed secret manager, KMS, or HSM; key-domain reuse, logging, test-key promotion, implicit fallback and silent schedule repair are forbidden. Multiple verification epochs exist only for a bounded zero-downtime rotation window; compromised epochs must be revoked rather than left until expiry.

`SignerOutcomeVerifier` is a trust-bearing interface, not a boolean supplied by the caller. A production implementation must authenticate the provider/KMS/HSM response and bind the complete typed evidence to the exact operation and dispatch. It must not accept a receipt, signature or digest merely because it is nonzero or structurally well formed. The actual signature bytes are retained in authenticated evidence so a digest cannot stand in for provider authenticity.

Secrets, raw tokens, key handles, user payloads, raw provider credentials and unredacted evidence are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, provider-verification or externally reachable boundary requires the appropriate threat, fuzz, timing and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-crypto-provider/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-crypto-provider/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-crypto-provider/Cargo.toml --all-targets --locked -- -D warnings
```

The unit corpus covers all six domains, non-overlapping sign windows, bounded verification overlap, exact-epoch no-fallback, emergency revoke, retirement, full-window terminal archival, capacity-full compromise recovery, archived key-ID rejection, signer archive-slot reservation, journal-epoch rollover, restart restore, archived receipt replay rejection, operation-ID reuse fencing, revision/clock/audit exhaustion atomicity and debug redaction.

Signer-journal hostile cases additionally cover forged outcomes, wrong request/provider/endpoint/attempt, signature-byte and digest substitution, response-loss reconciliation, current and archived receipt reuse, first/successor checkpoint shape and predecessor binding. Deterministic verifier fixtures prove that the source boundary invokes the verifier and preserves state on rejection; they do not prove a real provider response format, KMS/HSM authentication, durable database atomicity or cross-node convergence.

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, local-only execution and self-review do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, provider or operational boundaries.

## Operations

Expose provider latency, cache refresh, active epoch, bounded verification-epoch count, revoke, retirement, signer dispatch/outcome verification, reconciliation, receipt conflict, checkpoint sequence, archive health, deadline failures and schedule revision without secret, signature, receipt or handle labels.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, durable recovery, emergency revoke propagation, provider-outcome verification failure handling and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Real provider integration, durable rotation/revoke convergence, authenticated provider-outcome implementation, KMS/HSM failure injection, checkpoint-chain restore, timing analysis, large fuzz corpora, independent security review and penetration evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P1-CRYPTO-002`
- `GAP-P1-REVIEW-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, a durable provider/lifecycle integration, an independently reviewed real `SignerOutcomeVerifier`, authenticated checkpoint-chain restore and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.

```text
production provider = false
durable lifecycle = false
real outcome verifier = false
security review accepted = false
production ready = false
```
