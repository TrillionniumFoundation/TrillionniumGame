# trnm-token-crypto-provider

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-token-crypto-provider`  
Workspace class: `isolated`  
Lifecycle: `security-critical-crypto-provider`  
Owner role: `security`

## Status and authority

This document is the current module-level engineering contract for `trnm-token-crypto-provider`. Its authority is limited to the module boundary described here: **opaque key-operation provider and deterministic lifecycle-routing boundary**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, real provider integration and the independent reviews required by the linked gaps.

## Responsibilities

- define opaque sign/verify operations that never return key bytes;
- preserve six distinct key domains: access, refresh, console, runtime HTTP, socket and authority;
- schedule nonzero monotonic epochs with non-overlapping signing windows;
- select one exact active signing epoch and one exact requested verification epoch;
- bound active records, verification overlap and audit growth;
- revoke immediately and monotonically;
- retire only revoked or verification-expired records while preserving the epoch high-watermark;
- expose bounded handle-free lifecycle status and health.

Non-goals: this crate does not define JWT format, implement HS256, expose raw production keys, persist schedules, distribute rotation state between nodes, or substitute a real KMS/HSM deployment.

## Architecture and dependencies

JWT and token services call opaque operations through this boundary. Local deterministic providers are development-only profiles. `KeyEpochRegistry` stores routing metadata only: domain, opaque handle, epoch and time windows. Provider calls, durable persistence and cross-node convergence remain outside the registry.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Unknown domain or epoch fails closed. No verification lookup falls back to another epoch or domain. Provider calls have an external deadline contract. Raw key bytes are never represented by this API. Revoke is monotonic and idempotent.

Key handles are redacted from Debug output. A handle may be inspected explicitly only by code already holding the `KeyHandle`; generic diagnostics for `KeyHandle`, `KeyReference`, lifecycle windows and registries do not print its value.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Lifecycle invariants

- epochs are greater than zero and strictly increase per domain, including after retirement;
- signing windows use half-open intervals and never overlap inside one domain;
- Verification epochs are bounded to two simultaneous windows per domain;
- verification always names an exact epoch and does not search for an alternative;
- an emergency revoke blocks signing and verification immediately;
- retirement is permitted only after revocation or verification expiry;
- every mutation carries an exact expected revision and non-regressing mutation time;
- stale revision, clock regression, capacity exhaustion, audit exhaustion and revision overflow fail before state mutation;
- configured records are limited to eight per domain and audit events to 256;
- status and health expose counts and epoch numbers, never handles, tokens or credentials.

The in-memory registry is a deterministic source component, not a production lifecycle database. A durable adapter must atomically store revision, high-watermarks, windows, revocations, retirements and audit receipts before this boundary can be shared by multiple nodes.

## Correctness and failure model

Active/verification epoch transitions are deterministic, bounded and revision-fenced. Two writers starting from the same revision cannot both apply through a conforming durable adapter. Unknown epochs, stale snapshots, out-of-order clocks and exhausted counters fail closed.

All inputs, loops, retries, batches, queues, allocations, and shutdown paths are bounded. Unexpected states fail closed. Duplicate, stale, timeout, cancellation, restart, partial-failure and cross-node convergence behavior must be represented in deterministic and live tests where applicable.

## Security and privacy

Production requires a reviewed secret manager, KMS, or HSM; key-domain reuse, logging, test-key promotion, implicit fallback and silent schedule repair are forbidden. Multiple verification epochs exist only for a bounded zero-downtime rotation window; compromised epochs must be revoked rather than left until expiry.

Secrets, raw tokens, key handles, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, timing and independent review.

## Build and test

```bash
cargo fmt --manifest-path crates/trnm-token-crypto-provider/Cargo.toml -- --check
cargo test --manifest-path crates/trnm-token-crypto-provider/Cargo.toml --all-targets --locked
cargo clippy --manifest-path crates/trnm-token-crypto-provider/Cargo.toml --all-targets --locked -- -D warnings
```

The unit corpus covers all six domains, non-overlapping sign windows, bounded verification overlap, exact-epoch no-fallback, emergency revoke, retirement, revision/clock/audit exhaustion atomicity and debug redaction.

This isolated workspace is explicitly registered in package authority and must execute in the stable aggregate merge gate. Empty discovery, skipped mandatory tests, warnings, older-head results, local-only execution and self-review do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

## Operations

Expose provider latency, cache refresh, active epoch, bounded verification-epoch count, revoke, retirement, health, deadline failures and schedule revision without secret or handle labels.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, durable recovery, emergency revoke propagation and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Real provider integration, durable rotation/revoke convergence, KMS/HSM failure injection, timing analysis, large fuzz corpora, independent security review and penetration evidence remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P1-CRYPTO-002`
- `GAP-P1-REVIEW-001`
- `GAP-P0-CI-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, a durable provider/lifecycle integration and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.

```text
production provider = false
durable lifecycle = false
security review accepted = false
production ready = false
```
