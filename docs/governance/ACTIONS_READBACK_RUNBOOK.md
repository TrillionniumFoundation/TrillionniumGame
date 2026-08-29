# GitHub Actions and ruleset read-back runbook

Status: binding administrator procedure for `GAP-P0-CI-001`, `GAP-P0-GOV-001` and `GAP-P1-REVIEW-001`.

## 1. Safety boundary

Do not interpret a workflow file, a local command, a relay run, an empty collection, an older commit, a skipped job or an administrator screenshot as target-repository proof.

Repository Actions policy must not be broadened to arbitrary third-party actions. The aggregate workflow currently requires these immutable pins:

```text
actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e
actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
```

The workflow also installs Rust through `rustup`; it does not require an unpinned Rust setup action.

## 2. Enable Actions without weakening source policy

Using an organization/repository administrator identity:

```bash
gh api --method PUT \
  repos/TrillionniumFoundation/TrillionniumGame/actions/permissions \
  --input - <<'JSON'
{"enabled":true,"allowed_actions":"selected"}
JSON

gh api --method PUT \
  repos/TrillionniumFoundation/TrillionniumGame/actions/permissions/selected-actions \
  --input - <<'JSON'
{
  "github_owned_allowed": false,
  "verified_allowed": false,
  "patterns_allowed": [
    "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
  ]
}
JSON
```

If organization policy forbids the repository setting, change the organization policy through an independently reviewed administrator action. Do not switch to `allowed_actions=all` merely to obtain a green run.

## 3. Trigger the exact candidate

A workflow dispatch is recognized only when the workflow is present in an eligible repository ref. For a pull request, push a reviewed no-op-free source or documentation change to the PR head, or close/reopen the PR when appropriate. Record the exact head before and after triggering.

Never force-push `main` and never manufacture a status/check through the Status API.

## 4. Required run inspection

For candidate `<SHA>`:

```bash
gh api "repos/TrillionniumFoundation/TrillionniumGame/actions/runs?head_sha=<SHA>&per_page=100"
gh api "repos/TrillionniumFoundation/TrillionniumGame/commits/<SHA>/check-runs?per_page=100"
```

Acceptance requires:

- at least one workflow run with `head_sha=<SHA>`;
- terminal `status=completed` and `conclusion=success`;
- aggregate check named exactly `trillionnium-game-merge-gate`;
- all required child jobs present and successful;
- no missing matrix component;
- run/job/artifact IDs and response digests recorded in an observation manifest.

Run the repository verifier:

```bash
GITHUB_TOKEN=... python3 scripts/check-repository-governance.py \
  --live \
  --sha <SHA> \
  --output run/governance/<SHA>.json
```

The command fails when any required read-back fact is false.

## 5. Activate main protection only after stable check identity exists

Once a successful exact-head aggregate check has been observed, activate branch protection or an organization ruleset satisfying `docs/governance/GITHUB_ADMIN_ACCEPTANCE.json`:

- direct push, force-push and deletion blocked;
- strict latest-head aggregate check required;
- at least one approval;
- stale approvals dismissed;
- CODEOWNERS review required;
- last-push approval required;
- conversations resolved;
- linear history required;
- no unreviewed bypass actor.

Re-run the live verifier after mutation. An HTTP success response from the mutation endpoint is not enough; read-back must report `accepted=true`.

## 6. Independent reviewer mapping

Replace fallback-only ownership with named users or teams for:

```text
database
security
protocol
realtime
```

The implementer cannot satisfy their own required domain approval. Record the reviewer identity, role, reviewed head and decision in evidence.

## 7. Evidence admission

The observation file is diagnostic until it is:

1. bound to the exact candidate commit/tree;
2. hashed and uploaded as a target-repository artifact;
3. entered once in `docs/evidence/index.json`;
4. independently reviewed;
5. unexpired;
6. accepted by the gap and gate derivation checks.

Enabling Actions or branch protection closes only repository-control prerequisites. It does not grant C1–C5, SG1–SG9, compatibility, production, public-online or replacement credit.
