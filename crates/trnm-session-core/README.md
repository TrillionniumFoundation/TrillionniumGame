# trnm-session-core

Status: **module documentation; source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-session-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `identity`

## Status and authority

This document is the current module-level engineering contract for `trnm-session-core`. Its authority is limited to the module boundary described here: **session-family state model**. Source presence, a passing unit suite, or this document alone does not establish compatibility, durability, security, operational, or production acceptance.

The module's current maturity is `source-candidate`. Promotion requires exact-candidate execution, retained evidence, and the independent reviews required by the linked gaps.

## Responsibilities

Refresh-family state, atomic in-memory rotation and replay-triggered revocation.
The caller must derive and persist invalidation work; this library does not emit
an event/outbox record or perform socket invalidation itself.

Non-goals: It does not parse JWTs, store session rows, disconnect sockets, or expose public network endpoints.

## Architecture and dependencies

The core consumes verified identities and emits deterministic state transitions; persistence and realtime adapters implement durable storage and fanout.

Dependency direction is reviewed as part of package authority. This module must not introduce hidden global state, untracked background work, unbounded queues, or transport/database coupling outside the declared lifecycle.

## Public contracts

Concurrent refresh cannot create two valid successors. Replay of a consumed refresh token revokes the family and requires connected-session invalidation.

Public Rust types, serialized fields, configuration keys, database predicates, and externally observable error classes are change-controlled. A breaking change requires an explicit migration or compatibility decision and updated tests in the same candidate.

## Correctness and failure model

Family ownership is singular, rotation is atomic, revocation is monotonic, and unknown state fails closed without revealing account existence.

Returned rotation failures preserve all fields, except the explicitly specified
consumed-token replay, which revokes the family before returning an authentication
error. Process abort/allocation failure is not a recoverable Result guarantee.
The consumed-token set has no internal capacity/expiry control in this candidate;
its production lifetime and persistence bounds remain adapter obligations.

## Security and privacy

No raw token or signing key is logged. Token parsing and cryptographic verification occur in reviewed adapters before invoking this model.

Secrets, raw tokens, user payloads, receipts, and provider credentials are not logged or used as metric labels. Any new cryptographic, parser, unsafe, native, or externally reachable boundary requires the appropriate threat, fuzz, and independent review.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-session-core --all-targets --locked
cargo clippy --package trnm-session-core --all-targets --locked -- -D warnings
```

The root workspace and the stable aggregate merge gate must execute these targets. Empty discovery, skipped mandatory tests, warnings, older-head results, and local-only execution do not earn remote verification or claim credit.

Focused vectors and live/fault/differential suites are required when this module's behavior crosses protocol, database, security, realtime, or operational boundaries.

### State and public API

`SessionFamilyId` and `RefreshTokenId` are 16-byte identities, not JWT strings or
secret material. `SessionGeneration` is a checked `u64` counter. `new` rejects
zero family/token identities, starts generation at zero, makes the supplied token
active and initializes an empty consumed-token set. The core does not check user
identity, signature, expiry, clock skew or token entropy.

| Method | Result / side effect |
| --- | --- |
| `family_id`, `generation`, `status`, `active_token` | Read the current local state without mutation. |
| `verify_active(token)` | Requires Active then exact current-token equality. It neither consumes a token nor detects replay by consulting history. |
| `rotate(presented, replacement)` | Produces the previous/current generation and new active identity on success, or the ordered error below. |
| `revoke(reason)` | Writes `Revoked(reason)`. No method reactivates a family. Repeated explicit revoke calls can replace the stored reason; they do not restore Active. |

### Ordered transition and error table

Checks run in the following order. All failures have `RetryClass::Never`; the
`reason` is an internal diagnostic, not a public account-enumeration response.

| Priority / condition | Stable code / reason | State after call |
| --- | --- | --- |
| Already revoked | `Unauthenticated / session_family_revoked` | Entire state unchanged, including existing reason. |
| Presented or replacement token is zero | `InvalidArgument / zero_refresh_token` | Unchanged. |
| Presented token belongs to consumed history | `Unauthenticated / refresh_replay_detected` | Only status becomes `Revoked(RefreshReplay)`; no generation/token replacement. |
| Presented token is not the active token | `Unauthenticated / refresh_token_unknown` | Unchanged. |
| Replacement is active or already consumed | `AlreadyExists / replacement_refresh_token_reused` | Unchanged. |
| Current generation is `u64::MAX` | `OutOfRange / counter_overflow` | Unchanged; no token consumed, substituted or silently wrapped. |
| All checks pass | `RotationReceipt` | Old active token inserted once into history; replacement becomes active; generation advances once. |

The generation increment is computed before the first token-state write. A
transition from `MAX-1` to `MAX` remains legal; the next fresh-token attempt fails
without mutation. Replay is checked before that increment, so reaching the
counter ceiling cannot suppress revocation of a valid consumed-token replay.
Zero-token validation continues to precede replay detection; this change does
not redefine the error precedence for malformed input.

### Ownership, persistence and recovery obligations

`&mut self` provides exclusive mutation of one Rust value, not a distributed
lock or database transaction. `RefreshFamily` is Clone; independently mutated
clones can each rotate unless the adapter enforces one authoritative writer.
The adapter must atomically compare-and-update family status/generation/token
state and consumed identity, and persist a replay-triggered revocation even
though `rotate` returns Err. A blanket rollback-on-Err wrapper would discard that
intentional security transition and is therefore insufficient.

Do not send a successor credential before durable commit. This core has no
command ID, durable rotation receipt or response-loss replay method. A retry with
a consumed predecessor is intentionally treated as replay, not idempotent success;
a compatible response-loss design requires a separately specified durable
operation identity and profile/oracle review. The library does not implement that
adapter, refresh token signing, access-epoch propagation, socket fanout, restart
loading, expiry or garbage collection. The current API cannot directly import an
arbitrary generation; `u64::MAX` test fixtures are private boundary fixtures, not a
claim of a practical network attack or an observed production incident.

Memory grows with distinct consumed identities; insertion/lookups use a BTreeSet.
There is no internal retention cap. A production adapter must bound the family
lifetime/history without forgetting a still-replayable token or reactivating a
revoked family. No arbitrary eviction policy is introduced by this repair.

### Regression mapping

Existing `refresh_rotation_advances_generation_and_replaces_active_token`,
`replay_of_consumed_refresh_token_revokes_entire_family`,
`unknown_refresh_token_does_not_rotate_or_revoke_family`,
`logout_revocation_is_terminal` and
`replacement_token_cannot_reuse_current_or_consumed_identity` remain unchanged.

| Invariant | Added compiled-test target in `src/lib.rs` |
| --- | --- |
| Overflow errors preserve all fields and can be repeated safely | `generation_overflow_preserves_entire_family` |
| Last legal increment succeeds; later attempt does not wrap or mutate | `final_generation_transition_succeeds_without_wrapping` |
| Invalid, unknown and reused inputs leave all state intact | `ordinary_rotation_rejections_preserve_all_fields` |
| Replay at the ceiling still revokes and does not rotate | `replay_at_generation_ceiling_still_revokes_without_rotating` |
| Every revoked reason remains intact on a rejected rotation | `revoked_rotation_keeps_existing_reason_and_token_state` |

These are unit-test specifications, not evidence of their execution. Exact-source
format/test/lint and independent review must be recorded separately. The package
still has no live database, SDK differential or socket-disconnect acceptance.

## Operations

Adapters must expose refresh, replay, revoke, fanout, and failure metrics with bounded cardinality and auditable event identifiers.

The owning adapter or process must define readiness impact, drain behavior, metrics, alerts, capacity limits, and failure recovery before the module can be part of a production profile.

## Compatibility and evidence

Database repository, server middleware, socket revocation fanout, migration ownership, and immutable-oracle differential remain open.

Evidence must bind the exact repository, source commit, tree, workflow/run/job/attempt, environment, commands, assertions, retained artifact digests, limitations, expiry, and independent review decision.

## Known gaps and exit criteria

Blocking gaps:

- `GAP-P0-CRYPTO-001`
- `GAP-P0-SERVER-001`
- `GAP-P0-DATA-001`

Exit requires every applicable close criterion in `docs/status/GAP_REGISTER.json`, exact-head and prospective-merge execution, and conflict-free independent review. Temporary prototypes and gates also require an explicit convergence or removal decision.
