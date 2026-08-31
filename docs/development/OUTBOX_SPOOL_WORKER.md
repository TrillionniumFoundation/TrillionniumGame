# Outbox spool worker source candidate

Status: source candidate. This document grants no Nakama compatibility, production,
public-online, cutover, retirement, or external-effect delivery credit.

## Purpose

`trnm-outbox-worker` closes the missing process boundary between the transactional
`trnm_outbox` table and a durable, idempotent handoff. It claims fenced leases from
the authoritative PostgreSQL/CockroachDB schema and materializes each intent as one
content-addressed JSON record in a configured spool directory.

The worker supports:

- `check-config`: validate and print a redacted configuration;
- `run-once`: claim and process one bounded batch;
- `serve`: poll continuously until an optional stop file is created.

## Delivery contract

For each lease, the worker writes a temporary record, calls `sync_all`, creates the
final path with an atomic same-filesystem hard link, removes the temporary path, and
syncs the directory on Unix. The final filename is the lowercase intent ID and the
receipt digest is SHA-256 over the exact final bytes. Final bytes contain only stable
intent identity and payload fields; attempt, lease generation, owner and expiry are
deliberately excluded so a post-write/pre-ack crash can be reclaimed without creating
a false conflict.

A repeated delivery of the same intent is successful only when the existing bytes
match exactly. A conflicting regular file, symlink, directory, or different payload
fails closed and enters the bounded retry/dead-letter transition. If the durable
spool write succeeds but the database acknowledgement is lost, a later lease reclaim
revalidates the same bytes and can safely complete the original intent.

## Database and retry boundary

- PostgreSQL and CockroachDB use the existing bounded `PgPool` and statement-timeout
  policy.
- Plaintext requires explicit candidate-only opt-in.
- Verify-full TLS supports custom roots and a paired certificate/PKCS#8 client identity.
- Claim batch size, lease duration, attempt count, polling interval, pool size, acquire
  timeout, statement timeout, and maximum backoff are bounded.
- The database `attempt` column is `BIGINT`; every query parameter, row decode and
  update now uses Rust `i64`, while the public worker limit remains `u32` and is capped
  at the schema's maximum of 32 attempts.
- Retry delay is deterministic per intent/owner/generation and lies between half and
  all of the capped exponential backoff.
- A complete claim → durable spool → acknowledgement unit retries only explicit
  `SafeImmediate`/`SafeBackoff` database failures, with five attempts and a
  5–100 ms bounded exponential delay. Stable spool bytes make retries after a
  committed file write idempotent.
- Stale owner/generation completion remains fenced by the repository transaction.

## Required environment

```text
TRNM_OUTBOX_DATABASE_URL
TRNM_OUTBOX_DATABASE_PROFILE=postgresql|cockroachdb
TRNM_OUTBOX_NODE_ID_HEX=<32 lowercase hex characters>
TRNM_OUTBOX_SPOOL_DIRECTORY=<directory>
```

For local plaintext evidence only:

```text
TRNM_OUTBOX_DATABASE_TLS_MODE=plaintext-candidate
TRNM_OUTBOX_ALLOW_PLAINTEXT_DATABASE=1
```

For verified TLS:

```text
TRNM_OUTBOX_DATABASE_TLS_MODE=verify-full
TRNM_OUTBOX_DATABASE_TLS_ROOT_CERT_PEM=<optional PEM path>
TRNM_OUTBOX_DATABASE_TLS_IDENTITY_CERT_PEM=<optional PEM path>
TRNM_OUTBOX_DATABASE_TLS_IDENTITY_KEY_PKCS8_PEM=<paired PKCS#8 PEM path>
```

Operational bounds may be changed through the `TRNM_OUTBOX_BATCH_SIZE`,
`TRNM_OUTBOX_LEASE_DURATION_MS`, `TRNM_OUTBOX_MAX_ATTEMPTS`,
`TRNM_OUTBOX_POLL_INTERVAL_MS`, `TRNM_OUTBOX_MAX_BACKOFF_MS`, and
`TRNM_OUTBOX_DATABASE_*TIMEOUT*` variables. `TRNM_OUTBOX_STOP_FILE` enables a
portable cooperative stop request.

## Source validation

The root workspace must auto-discover the binary and run its unit tests under the
same exact Rust toolchain as the canonical server:

```bash
cargo fmt --all -- --check
cargo test --package trnm-persistence-pg --all-targets --locked
cargo clippy --package trnm-persistence-pg --all-targets --locked -- -D warnings
```

The full aggregate merge gate remains mandatory because the worker changes the shared
persistence package and its target inventory. The dedicated dual-profile workflow
executes normal delivery, a real process exit after durable spool and before database
acknowledgement, expired-lease reclaim by a distinct node identity, conflicting-receipt
dead-letter, and two-process claim exclusion against immutable PostgreSQL and
CockroachDB images. This remains single-host process-fault evidence, not multi-node HA.
A repair runner passing those scenarios before writing a commit is not a substitute for
the final exact-head workflow collection.

## Review gate

Moving the pull request out of Draft is only a routing transition. It does not count
as independent acceptance. The reviewer must inspect the final commit and tree, the
non-empty exact-head jobs, the deterministic diagnostic digests, the BIGINT type fix,
and the lease-reclaim semantics. Any later source push invalidates prior approval and
requires a fresh workflow collection and review decision.

## Remaining gap boundary

This source candidate does not yet close `GAP-P1-OUTBOX-001` or `GAP-P1-PG-001`.
Closure still requires accepted exact-head live database and fault evidence,
process/node loss reclaim, pool saturation, TLS expiry/rotation, long-running
endurance, independent data-integrity/security review, and real Rust handlers that
consume the spool records for broadcast, search, notification, completion, and
external provider effects.

## Final-attempt test failpoints

`TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY=1` exits with code `71` after claim and
before publication. `TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY=1` exits with code
`70` after durable publication and before acknowledgement. Both require
`TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS=1`, are mutually exclusive and are valid
only for `run-once`.
