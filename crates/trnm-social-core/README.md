# trnm-social-core

Status: **module documentation; social-domain-source-candidate; no automatic compatibility or production credit**  
Path: `crates/trnm-social-core`  
Workspace class: `root`  
Lifecycle: `product-library`  
Owner role: `social`

## Status and authority

This crate is the transport- and persistence-independent social-domain state machine for the second Plan v3.2 surface tracked by issue #138. It defines a bounded source candidate for friend relationships, blocking, group membership, ordered group chat, notifications, command receipts and post-commit outbox intents.

Its authority ends at deterministic in-memory transitions. It is not a complete Nakama users/social API, network adapter, database implementation, distributed presence service, notification delivery worker, official-SDK compatibility result or production social authority.

## Responsibilities

The crate owns typed social command, fingerprint, group, message and notification identities; friend request, acceptance, removal, block and unblock transitions; stable friend pagination; group creation, open or approval-based join, role change, leave, ban and unban; contiguous group-message sequencing; ordered notification read/delete state; exact command replay; and bounded outbox intent construction.

Every collection and payload has a validated finite limit. Accepted mutations are applied to a cloned candidate and committed only after reverse relationships, membership sets, sequence continuity, receipt/outbox cardinality and ownership references pass invariant validation.

Non-goals include user profile search/import, direct and room chat types, wire cursor encoding, public HTTP/gRPC/RTAPI mapping, durable persistence, cross-node presence/fanout, push providers, moderation policy, history retention, data export/deletion and migration.

## Architecture and dependencies

`trnm-social-core` depends only on `trnm-identity-core` for the canonical `AccountId`. It performs no network, database, filesystem, clock, random, telemetry or provider I/O.

Public adapters authenticate the caller, validate project and provider context, decode stable wire fields and call typed service commands. Durable repositories must atomically persist social state, the exact command receipt and outbox intents. Realtime and notification workers consume committed intents only after the source transaction succeeds.

No external delivery may occur inside the mutable transaction. A mutable Rust value proves exclusive access only to one process value; production adapters must enforce one writer, serializable conflict handling, restart recovery and migration fencing.

## Public contracts

All-zero identifiers are rejected. Group names, message bodies and notification text are bounded, and payload-bearing debug output exposes byte length rather than content. Page sizes are positive and capped.

A social command ID is idempotent only for the exact original fingerprint, actor and operation family. Exact replay returns the original receipt and produces no duplicate state or outbox intent. Cross-actor or cross-operation reuse fails as a conflict even when a caller repeats the same fingerprint; reuse with a changed fingerprint also fails without mutation. Trusted adapters must derive the fingerprint from the canonical complete command, including every target, group, message, notification and payload field, rather than accepting an arbitrary caller-selected value.

Relationships are mutually exclusive across friendship, pending request and directional block state. A block removes friendship and pending requests. Group roles are ordered; at least one superadmin remains; a member cannot be both active, pending and banned. Group message sequence begins at one and remains contiguous.

Friend, message and notification cursors are scoped to their owner, group or recipient. The current in-memory cursors provide deterministic keyset traversal but not a durable snapshot across concurrent mutations; the public compatibility profile and oracle must decide exact encoding and mutation semantics.

## Correctness and failure model

Validation, unknown identity, self-relationship, collision, stale command fingerprint, actor mismatch, operation mismatch, capacity exhaustion, permission failure, last-superadmin protection, duplicate message/notification identity and checked counter overflow fail before committing the candidate. Receipt invariants require every recorded actor to remain a registered user.

The receipt records actor, global social revision, outcome and exact outbox count. Outbox IDs are derived from command identity plus a positive ordinal, preventing one accepted command from silently creating duplicate intent identities.

Crash safety, response-loss reconciliation and external exactly-once effects are not properties of this in-memory crate. A durable adapter must commit domain mutation, receipt and intents together; workers must lease, fence, reconcile and terminally classify delivery according to the repository outbox contract.

## Security and privacy

Account IDs, group IDs and external wire inputs are untrusted. Authentication, project isolation, user status, provider assertions, abuse/rate limits and visibility policy must be enforced by trusted adapters before mutation.

Message and notification payloads are never metric labels. `Debug` for payload-bearing wrappers is redacted to byte length. Production logs, traces and evidence must not expose private messages, notification contents, access tokens, provider credentials or high-cardinality user identities.

Group administration, blocking, moderation, notification generation and history access require explicit authorization and negative tests. Cross-project access, account enumeration and stale session or route generations must fail closed.

## Build and test

```bash
cargo fmt --all -- --check
cargo test --package trnm-social-core --all-targets --locked
cargo clippy --package trnm-social-core --all-targets --locked -- -D warnings
```

The focused corpus covers actor-and-operation-scoped exact receipt replay, changed fingerprints, friendship transitions, mutual blocking, keyset pagination, approval joins, role and last-superadmin rules, bans, chat sequencing, cursor scope, notification ordering/read/delete, capacity failures, checked limits and payload redaction. Hostile receipt tests prove that a command/fingerprint pair cannot be replayed through another actor or operation and that the two valid `join_group` outcomes remain in one operation family.

These tests establish only source-level deterministic behavior. Required exact-head and prospective-merge CI, PostgreSQL and CockroachDB live profiles, protocol/SDK differentials, reconnect/failure injection and independent specialist review remain separate.

## Operations

This crate starts no listener, worker or background task. Adapters should expose bounded low-cardinality metrics for accepted/rejected social operations, relationship conflicts, group membership changes, capacity pressure, message sequence failures, notification lifecycle, command replay and outbox backlog.

The production composition must define readiness dependencies, drain behavior, queue and fanout budgets, retention, moderation/audit, database deadlines and cancellation, worker retry/reconciliation, reconnect behavior and incident recovery.

## Compatibility and evidence

No Nakama compatibility credit is claimed. Closure of issue #138 requires every in-scope DEN-API, DEN-RTAPI, DEN-DATA and DEN-DB leaf to map to handlers, services, repositories and tests; exact HTTP/gRPC/WebSocket and official-SDK differential against the immutable oracle; and profile-specific database, restart, response-loss and outbox evidence.

Provider delivery, distributed presence, direct/group/room chat distinctions, import semantics, list ordering/defaults, cursor bytes, public error details, hook ordering and notification codes remain explicit compatibility decisions rather than assumptions.

## Known gaps and exit criteria

Blocking work remains under `GAP-P0-SERVER-001`, `GAP-P0-DATA-001`, `GAP-P0-SCOPE-001`, `GAP-P1-REVIEW-001`, parent issue #129 and child issue #138.

Exit requires complete typed service and durable repository adapters, public protocol coverage, distributed realtime integration, restart/reconnect and failure evidence, exact denominator mapping, official consumer/oracle differential, retained exact-object artifacts and conflict-free independent protocol, data-integrity, realtime and security acceptance.

```text
source_candidate=true
wire_differential=false
durable_database=false
distributed_delivery=false
accepted_evidence=false
complete_nakama_compatibility=false
production_ready=false
```
