# Denominator review and lock controller

Status: **candidate control; this change does not review or lock any denominator**

The controller converts exact candidate bytes plus a separate human review bundle into one of:

```text
reviewed-blocked
reviewed-ready
reviewed-locked
```

A lock requires exact leaf coverage, unchanged signature hashes, owner/task/test/gate/evidence binding, author/reviewer separation, manual-contract disposition, and a non-empty successful exact-head workflow artifact. A versioned exclusion requires an ADR, future expiry and two reviewers. A denominator decrease requires both an ADR and an upstream-delta digest.

Even a complete fourteen-family aggregate returns only:

```text
sg1-independent-gate-review-required
```

It never sets `sg1_complete=true` and grants no compatibility or production credit. Restricted Console material, unresolved manual contracts and absent remote evidence remain explicit blockers.
