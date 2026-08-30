# Evidence index contract

`docs/evidence/index.json` is the only index through which an artifact may influence a gap, product gate, compatibility claim or release decision.

An indexed record must identify:

- evidence ID and evidence type;
- producer repository, workflow/run/job and artifact identity;
- target repository, exact commit and exact tree;
- environment and fixture identities;
- command/assertion result and divergence set;
- artifact digest and size;
- limitations and expiry;
- independent reviewer identity, role, decision and review time;
- affected gap, task, parity, gate and claim IDs.

The index is fail closed:

- missing, empty, skipped, cancelled, interrupted, expired or revoked evidence has no credit;
- a relay run has no credit when its target commit/tree differs from the candidate;
- author self-approval has no credit;
- an unreviewed source fix remains a source candidate;
- no P0/P1 gap closes without accepted independent review;
- identity, ACL, sequence, money, public version, cursor, error code and durable effect divergences cannot be normalized.

Index validation does not prove the underlying workload ran. It only proves that the evidence record is complete, internally consistent and eligible for the explicitly declared scope.