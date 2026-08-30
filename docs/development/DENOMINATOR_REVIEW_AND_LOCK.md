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

## Exact-head remote evidence carriers

A reviewed lock may bind either of two fail-closed remote carriers:

1. `artifact`: a positive GitHub Actions artifact ID plus an exact SHA-256;
2. `immutable-job-log`: a positive workflow run/job identity whose successful log
   contains the deterministic evidence-archive SHA-256, with `log_sealed=true`.

The job-log carrier exists for repositories that deliberately avoid external upload
Actions. It does not weaken reviewer independence, candidate-head equality,
non-empty assertion counts, or successful terminal execution. Missing or older
heads, zero IDs, non-success conclusions, absent digests and unsealed logs are
rejected. Neither carrier grants SG1 by itself; two independent reviewers and the
global SG1 gate review remain mandatory.
