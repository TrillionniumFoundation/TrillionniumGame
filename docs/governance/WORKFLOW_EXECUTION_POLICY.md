# Workflow execution policy

Status: binding plan-v3 merge policy while the organization action allowlist blocks external actions before job creation.

## Required execution model

Every required workflow must:

1. derive the candidate repository, branch and SHA from the pull-request head or push event;
2. create a new Git repository in `GITHUB_WORKSPACE` using runner-provided `git`;
3. fetch only the exact branch head required for the event;
4. detach at `FETCH_HEAD` and require `rev-parse HEAD == CANDIDATE_SHA`;
5. execute repository scripts and runner-provided tools;
6. fail when a required lane is absent, skipped, cancelled or unsuccessful;
7. emit commit/tree, run ID and deterministic output digests into the immutable job log.

## External action boundary

External `uses:` entries are forbidden while the current organization policy can reject them before a job exists. Pinning an action SHA is necessary for supply-chain integrity but does not make it executable under that policy.

A future ADR may re-enable selected external actions only after:

- the organization/repository allowlist is read back and evidenced;
- the action commit, provenance, permissions and maintenance posture are reviewed;
- a malicious-input and pull-request-fork threat model is accepted;
- the aggregate gate remains non-empty on an exact test head.

Local repository actions or reusable workflows may be considered separately, but they must not introduce write credentials or weaken exact-head identity.

## Write permissions

Required validation workflows use `contents: read`. A workflow with any write permission is forbidden unless it is:

- explicitly listed in the machine policy;
- branch and event constrained;
- independently reviewed;
- temporary with a documented expiry;
- removed before the candidate is accepted.

The plan-v3 branch used temporary one-shot formatting/fix workflows. Each was restricted to the exact feature branch, executed verified tests before writing, and was deleted immediately after its source commit. No self-writing workflow is part of the final candidate.

## Artifact evidence

When the organization blocks `actions/upload-artifact`, deterministic evidence is sealed by:

- canonical tar ordering;
- fixed timestamps, owner and group;
- SHA-256 of the archive and constituent files;
- exact candidate repository/commit/tree/run identity in the job log.

This log-sealed evidence is useful for current-head validation but does not replace long-lived release evidence, independent review or retention requirements. Product-gate evidence must still be indexed under `docs/evidence/index.json` or a reviewed external evidence store.

## Enforcement

```bash
python3 scripts/check-workflow-action-policy.py
```

The aggregate merge gate executes this checker. Any new external action, `pull_request_target`, persistent credentials or unapproved write permission blocks merge.
