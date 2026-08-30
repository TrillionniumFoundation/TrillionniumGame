# PG-wire SERIALIZABLE retry contract

Status: source candidate. No database, durability, compatibility, HA or production credit.

Source:

```text
crates/trnm-persistence-pg/src/bin/trnm-pg-retry.rs
```

## Policy

The retry driver reconstructs the repository connection for each attempt and reuses the exact same command/entity/fingerprint/state/event/outbox identities. It retries only when the domain error is classified as:

- `SafeImmediate`, currently used for SQLSTATE `40001` serialization failure;
- `SafeBackoff`, currently used for deadlock or transient database transport/connection failures.

It never automatically retries:

- `Never` errors such as invalid arguments, uniqueness/foreign-key/constraint failures, changed command fingerprints, internal errors or data loss;
- `ResyncRequired` errors such as stale entity revision or authority generation.

## Hard bounds

Defaults:

```text
TRNM_DATABASE_MAX_ATTEMPTS=5
TRNM_DATABASE_TOTAL_DEADLINE_MS=10000
TRNM_DATABASE_BASE_BACKOFF_MS=10
```

Constraints:

- maximum configured attempts: 16;
- maximum individual backoff: 1000 ms;
- total elapsed time plus the next delay must remain below the total deadline;
- a successful first apply or exact duplicate receipt ends the loop;
- attempts, final reason and elapsed time are emitted without secrets.

The current source candidate uses deterministic exponential backoff. Production integration must add an approved bounded jitter source and cancellation propagation while preserving deterministic testability.

## Evidence matrix

Both PostgreSQL and CockroachDB must separately prove:

1. a forced serialization conflict returns the expected SQLSTATE/classification;
2. the exact command identity is reused;
3. one visible receipt/event/outbox effect exists after eventual success;
4. an exhausted attempt/deadline returns failure without an acknowledgement;
5. stale revision/generation is not retried;
6. changed fingerprint is not retried;
7. process restart and response loss replay the exact stored receipt;
8. retry counters and timings do not leak credentials or token material.

## Limitations

This binary is not yet bound to the HTTP/gRPC request deadline, uses the synchronous PG client, has no accepted pool/TLS/cancellation layer, and has no current-head live conflict artifact. It therefore cannot close `GAP-P1-PG-001`, `GAP-P0-DATA-001`, `GAP-P0-SERVER-001`, C2, C3, C4 or SG4 by itself.