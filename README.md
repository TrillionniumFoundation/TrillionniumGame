# Fixed obsolete-run cleanup

One-shot operational branch, not product source or an acceptance workflow.

The sole mutating API operation is the normal cancellation of run 33951787286, the queued prospective-merge attempt for c2944df1, which is the direct parent of the live be2be89e candidate. The script requires that exact current PR/base, the exact old run/event/workflow/repository/attempt, a complete nonempty set of still-unassigned queued jobs, and repeated pre-write checks. It never cancels a current candidate run, forces cancellation, deletes a run, changes a ref, modifies protection, approves or merges a PR.

The API can change after a read; this is not an atomic compare-and-cancel guarantee. The immutable fixed old run ID remains the only write target even in that race. If any guard fails, the operation stops, preserving diagnostic output. A 202 response is not completion; the final check requires observed cancelled status. No product/compatibility/gap credit is granted.

This workflow deliberately needs actions:write only for the scoped cancellation, plus contents:read and pull-requests:read for identity. It is separate from the existing read-only verification branch. Cancellation preserves the run record and is not a passing test or evidence acceptance. Removing obsolete queued work does not prove why current runner assignment is delayed or guarantee that it will recover.
