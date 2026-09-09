# Roadmap

Status: **authoritative current documentation**  
Revision: 2026-09-01

The machine execution queue is `docs/roadmap/NEXT_MILESTONE.json`. This document explains sequencing and team boundaries; it does not override machine status.

## 1. Current milestone

The active milestone is foundation control/data/server readiness. Its objective is to establish a trustworthy repository and one golden Rust vertical slice before broad domain expansion.

Current positive source frontier includes:

- plan/status/gap/evidence validators and a closed-world aggregate gate;
- one authoritative migration chain with PostgreSQL/CockroachDB profiles;
- authority revision/generation and exact command receipt behavior;
- transactional outbox claim/reclaim/terminal paths and fault harnesses;
- bounded database pool/TLS/timeout/retry source controls;
- access-token/session-family source integration;
- bounded HTTP framing, persistent WebSocket and narrow protobuf envelope;
- generated source for the pinned Nakama Healthcheck signature;
- response-loss, process and backup/restore workflow slices.

This frontier remains below complete vertical-slice acceptance because independent review, complete oracle differential, cancellation/rotation/performance evidence, denominator lock and governance enforcement are not all accepted.

## 2. Immediate execution order

### R0 — Documentation authority and control-plane convergence

Exit criteria:

- one current plan and nine current topic documents;
- all historical/versioned Markdown removed from the active tree;
- all active references migrated;
- documentation authority checker required by the plan/merge gate;
- plan, state, gap, evidence and gate validators all pass on the same head;
- no product claim is promoted by documentation cleanup.

### R1 — Exact-head CI and repository governance

Exit criteria:

- every required workflow has a non-empty terminal-success result for one unchanged head;
- aggregate rejects missing/startup/zero-job/older-head results;
- current candidate identity is exact and singular;
- `main` ruleset/branch protection, required aggregate and review policy are read back;
- negative merge rehearsal proves no bypass;
- named independent owners/reviewers are active.

### R2 — Data and security authority

Exit criteria:

- authoritative migration/adapter/backup digests agree for both profiles;
- pool acquisition, statement/lock timeout, cancellation and retry budgets are proven;
- outbox terminal, ambiguous outcome and reconciliation behavior are accepted;
- JWT/crypto provider choice, malformed corpus, key separation and rotation are reviewed;
- no acknowledged-write loss, duplicate visible value or stale writer acceptance.

### R3 — Single golden server vertical slice

Exit criteria:

```text
official SDK request
 -> HTTP/JSON + generated gRPC + persistent RTAPI
 -> session verification and authorization
 -> deterministic service command
 -> SERIALIZABLE transaction
 -> head + event + receipt + outbox
 -> acknowledgement after commit
 -> delivery/reconciliation
 -> response-loss/restart/reconnect
 -> PostgreSQL and CockroachDB
 -> immutable Nakama differential
```

The final process has one composition root, supervised lifecycle, bounded queues/deadlines and signal-driven drain. Protocol, database, security and SRE reviewers accept the exact evidence.

### R4 — SG1 and SG2 lock

Exit criteria:

- all 14 denominator families reproduced and independently accepted;
- zero unclassified leaves;
- every leaf maps owner/task/test/profile/evidence;
- immutable and instrumented oracles reproducible;
- normalizer registry approved;
- official SDK consumer matrix active.

Only after R0–R4 may the program open broad horizontal implementation without exception review.

## 3. Domain expansion sequence

### Phase A — Identity, storage and social foundation

- complete authentication providers, account/link/unlink and refresh/session lifecycle;
- storage ACL/OCC/list/search/version semantics;
- friends, groups, notifications and social hooks;
- migration and differential evidence for each profile.

### Phase B — Realtime and matchmaking

- distributed connection/route registry;
- presence, streams and chat history;
- reconnect cursor and revocation fanout;
- matchmaker tickets, parties and atomic party matching;
- client-relayed matches and cross-node data routing.

### Phase C — Authoritative runtime

- fixed-tick authoritative matches, placement and snapshots;
- capability-limited runtime host;
- Rust SDK, WASM, Lua and JavaScript/TypeScript profiles;
- Runtime RPC/hooks/jobs/module ordering;
- source migration of existing Go modules.

### Phase D — Value, Console and operations

- provider callbacks, IAP/subscriptions/refund/renewal/reconciliation;
- Console API, RBAC, MFA, audit and dangerous-action approval;
- full telemetry, privacy, abuse/rate limiting and security hardening;
- backup/PITR, HA, upgrades, capacity and endurance.

### Phase E — Migration and retirement

- snapshot/backfill/CDC and semantic comparator;
- shadow with no effects;
- exclusive canary ownership;
- Rust primary, Nakama read-only and rollback rehearsal;
- C5 support approval and Nakama retirement.

## 4. Parallel team model

Parallel work is allowed only behind stable contracts and explicit ownership. Suggested long-term lanes:

| Team | Primary ownership |
| --- | --- |
| program/compatibility | plan, denominator, oracle, evidence and release claims |
| server/platform | composition root, config, lifecycle, service interfaces |
| protocol/SDK | HTTP/gRPC/RTAPI, generated types and official consumers |
| identity/security | auth, sessions, keys, providers and privacy |
| data/migration | schema, repositories, outbox, backup/PITR and migration |
| realtime/distributed | connection actors, presence, routes, chat and ownership |
| multiplayer/runtime | matchmaker, parties, matches and runtime engines |
| Console/operators | Console API/UI, RBAC, audit and workflows |
| SRE/release | observability, performance, HA, canary and incident response |
| independent QA | differential, faults, security review and acceptance |

Each module has one owner and one stable public boundary. Cross-team changes use versioned contracts; teams do not bypass services to share database tables or mutable process state.

## 5. Critical dependency rules

- No domain expansion bypasses denominator ownership or the unique server/service boundary.
- No new public operation is accepted without its official protocol leaf and differential plan.
- No durable command is accepted without receipt/outbox/restart behavior.
- No external value effect is accepted without idempotency, ambiguous-outcome reconciliation and rollback policy.
- No database profile inherits another profile's evidence.
- No security-sensitive source advances without independent review.
- No migration phase permits dual writers.
- No release claim is derived from task/LOC/commit counts.

## 6. Milestone evidence

Every roadmap item maps to task, gap, parity and gate IDs in machine files. Promotion requires exact-head artifacts and the test classes defined in [`TESTING_AND_EVIDENCE.md`](TESTING_AND_EVIDENCE.md). The current milestone may close source/CI blockers without earning C1, SG1, SG4 or production authority.

## 7. Replanning

Re-estimate after SG1 and SG3 using actual leaf count, accepted throughput, defect escape and evidence lead time. Preserve the P50 48-month/P80 60-month baseline until data supports an approved revision. Scope changes require explicit denominator and plan updates; schedule pressure cannot redefine completion.

## 8. Completion boundary

The roadmap ends only after all mandatory leaves, migration, operations, support and retirement conditions in [`../CURRENT_PLAN.md`](../CURRENT_PLAN.md) are accepted. A locally runnable Rust server or a green foundation PR is an important milestone, not project completion.
