# Validation authority boundary

Validation results are classified as follows:

1. **source present** — files exist; no execution claim;
2. **local diagnostic** — commands ran in a non-authoritative local environment; useful for defects, no remote/gate credit;
3. **relay diagnostic** — another repository ran the exact target commit/tree and emitted a digest-indexed artifact; still not the target required check;
4. **target-native remote verified** — the target repository ran a non-empty terminal workflow against the exact current head;
5. **independently reviewed** — a non-author accepted the indexed evidence for a declared scope;
6. **gate accepted** — all dependencies, expiry, divergence and administrator readback rules pass.

A lower class never implies a higher class. In particular, source files, local test reports, relay runs, workflow definitions and issue/PR state cannot close an exact-head CI, governance, compatibility, durability, security, migration, production or retirement gap.