# Outbox final-attempt expiry reaper

Status: source and exact-head fault-evidence candidate. This document grants no
Nakama compatibility, production, public-online, cutover, replacement or retirement
credit.

## Failure mode

A worker can claim the final configured outbox attempt, durably publish the effect,
and then terminate before it acknowledges or retries the row. The row is left in the
leased state with `attempt == max_attempts`. The ordinary claim predicate deliberately
requires `attempt < max_attempts`, so an expired final-attempt row cannot be claimed
again and would otherwise remain stranded indefinitely.

## Transition

Every bounded `claim_outbox` transaction now first examines at most the requested
batch size of rows satisfying all of the following:

- `state = leased`;
- `available_at_ms <= now_ms`, which is the lease-expiry fence while leased;
- `attempt >= max_attempts`;
- the row remains at the exact observed lease generation and attempt.

Each matching row is locked and atomically moved to the existing dead-letter state.
The owner and receipt are cleared, a stable nonzero reason digest is stored, and the
compare-and-swap predicate repeats state, expiry, generation and attempt. A competing
transition therefore aborts or wins exactly once; it cannot create a second visible
spool record.

The fixed reason is SHA-256 of `outbox_expired_attempt_exhausted`:

```text
c57f69b9f67ddf67d5d6a49b4527af2e17ad313bfbc2f8397b5f9120541be25a
```

## Bounds and evidence

The reaper shares the existing maximum batch of 64 and maximum attempt count of 32.
It executes in the same serializable transaction that subsequently claims new work,
so no unbounded background scan or separate scheduler is required.

`outbox-final-attempt-reaper` executes the following profile against both immutable
PostgreSQL and CockroachDB images:

1. create a transactional outbox intent;
2. claim attempt one with `max_attempts = 1`;
3. terminate the real worker process after durable spool publication and before DB acknowledgement;
4. verify the row remains leased and the single spool record exists;
5. wait beyond the lease expiry;
6. run a new worker cycle;
7. verify the row is dead-lettered with owner and receipt cleared and a nonzero reason;
8. verify the spool bytes and visible file count are unchanged.

## Remaining boundary

This closes the source-level stranded-final-attempt defect only after the exact-head
workflow and independent data-integrity review are accepted. It does not prove
cross-host storage semantics, multi-node HA, a concrete broadcast/search/notification
consumer, long-running endurance or production delivery.
