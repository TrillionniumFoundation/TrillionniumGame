# WORLD-P0-004 A6/A7 execution

Status: implemented source candidate; exact-head and deployed evidence pending.

## A6 — production runtime integration

The target authority profile routes signed commands through:

```text
core preflight
→ durable reservation
→ bounded HTTPS World execution outside authority/storage locks
→ independent result verification
→ stale-fenced atomic core + World journal persistence
```

The legacy direct path remains an explicit separate profile. Target failure has no automatic legacy fallback.

## A7 — isolated deployed fault matrix

The exact branch defines an isolated PostgreSQL + Nakama/TrillionniumGame + TLS World fixture + response-drop proxy matrix covering:

- normal accepted commit;
- post-upstream response loss and exact request replay;
- external wait with PostgreSQL activity/lock capture;
- process exit after durable reservation;
- process exit after verified World result before commit.

A scenario is not passed until exact-head CI produces its report, atomicity records and SHA-256 manifest.

## Exclusions

Trillionnium Chain, Chain finality/inclusion, CEX settlement, public online and public player markets are outside this tranche and remain uncredited.
