# World command deployed runtime context v1

```text
player / agent
  → TrillionniumGame authenticated command
  → Nakama-owned preflight and durable reservation
  → HTTPS World deterministic transition
  → independent result verification
  → Nakama-owned stale fencing
  → atomic core snapshot + World journal storage batch
  → canonical event / later Nakama completion
```

World owns deterministic rules and unsigned result material. TrillionniumGame owns admission, roles, order, idempotency, recovery, roots and completion signing. Integration owns independent evidence locking. Trillionnium Chain is excluded.
