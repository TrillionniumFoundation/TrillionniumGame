# PostgreSQL-bound Rust server vertical slice v1

Status: **source candidate; execution evidence not yet accepted**.

## Purpose

The pure `trnm-server` candidate proves an executable ingress and authority flow, but its durable state is in memory. This slice puts a bounded Rust process directly on the production-authoritative PostgreSQL migration and `trnm-persistence-pg` adapter.

The entrypoint is an example target in the existing root workspace:

```text
crates/trnm-persistence-pg/examples/trnm_server_pg_slice.rs
```

Using an existing workspace target ensures the root all-target build/test gate must compile the process source without adding a parallel lockfile or hidden dependency graph.

## Exact scenario

`.github/workflows/pg-server-vertical-slice.yml` executes:

1. start the pinned PostgreSQL OCI image;
2. record image/container identity;
3. apply `migrations/postgresql/0001_foundation_up.sql` to an empty database;
4. build the exact root workspace/all targets with Rust 1.85.1 and `--locked`;
5. start the Rust process for one request;
6. commit command 1 at revision 0;
7. require one receipt, one event, one outbox intent and one command/outbox link;
8. let the process drain and exit;
9. restart a new process against the same database;
10. repeat command 1 and require an exact duplicate receipt with no new rows;
11. restart again and submit command 2 at stale revision 0;
12. require conflict and unchanged durable row counts;
13. record exact repository commit/tree, image, logs, responses, row counts and SHA-256 manifest.

The response is written only after `PgRepository::commit_command` returns from transaction commit. The duplicate path loads the durable receipt before revision validation, enabling response-loss/restart replay.

## What this proves when the exact workflow passes

Only the scoped PostgreSQL source candidate may advance to remote-verified for:

- real process ingress;
- authoritative migration application;
- command/event/outbox atomic commit;
- acknowledgement after commit;
- durable exact duplicate replay after process restart;
- stale revision rejection without duplicate durable effects.

## Remaining blockers

This slice does not provide:

- CockroachDB execution;
- TLS or connection pooling;
- bounded automatic serializable retry;
- ambiguous network loss injected between database commit and response write;
- production auth/session verification;
- gRPC or WebSocket JSON/protobuf;
- outbox lease/reclaim/delivery;
- immutable Nakama wire/behavior differential;
- HA, load, PITR or security evidence;
- independent database, protocol or security review.

## Claim boundary

Even a passing workflow cannot close `GAP-P0-SERVER-001`, SG4 or C1/C2 by itself. It is one evidence item under the complete vertical-slice closure contract.
