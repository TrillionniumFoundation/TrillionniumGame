#!/usr/bin/env bash
set -euo pipefail

repository=TrillionniumFoundation/TrillionniumGame
repository_id=1323087470
branch=main
confirmation=apply-TrillionniumGame-main-governance-v2
contract=docs/governance/REQUIRED_CHECKS.json

evidence=${1:-run/governance/main-protection-v2}
mkdir -p "$evidence"

if [[ ${TRNM_GOVERNANCE_CONFIRM:-} != "$confirmation" ]]; then
  echo "refusing governance mutation: set TRNM_GOVERNANCE_CONFIRM=$confirmation" >&2
  exit 64
fi
if [[ ! ${TRNM_EXPECTED_MAIN:-} =~ ^[a-f0-9]{40}$ ]]; then
  echo "TRNM_EXPECTED_MAIN must be the exact 40-character main SHA" >&2
  exit 64
fi

command -v gh >/dev/null
command -v jq >/dev/null
test -f "$contract"
gh auth status >/dev/null

api() {
  gh api --hostname github.com "$@"
}

jq -e \
  --arg repository "$repository" \
  --arg branch "$branch" \
  '.schema == "trillionnium.required-checks.v1" and
   .repository == $repository and
   .branch == $branch and
   .activation_policy.context_must_have_successful_exact_main_run_before_protection == true and
   .activation_policy.empty_skipped_cancelled_or_missing_counts == false and
   .activation_policy.strict_latest_head_required == true' \
  "$contract" >/dev/null

jq '[.contexts[] | select(.required == true and (.pull_request_only // false) == false) | .name]' \
  "$contract" >"$evidence/required-contexts.json"
context_count=$(jq 'length' "$evidence/required-contexts.json")
if [[ "$context_count" -lt 1 ]]; then
  echo "required-check contract produced no main protection contexts" >&2
  exit 64
fi

api "/repos/$repository" >"$evidence/repository-before.json"
jq -e \
  --argjson id "$repository_id" \
  --arg full "$repository" \
  '.id == $id and .full_name == $full and .default_branch == "main" and .archived == false' \
  "$evidence/repository-before.json" >/dev/null

api "/repos/$repository/branches/$branch" >"$evidence/branch-before.json"
actual_main=$(jq -r '.commit.sha' "$evidence/branch-before.json")
if [[ "$actual_main" != "$TRNM_EXPECTED_MAIN" ]]; then
  echo "main drift: expected $TRNM_EXPECTED_MAIN, observed $actual_main" >&2
  exit 65
fi

api \
  -H 'Accept: application/vnd.github+json' \
  "/repos/$repository/commits/$actual_main/check-runs?per_page=100" \
  >"$evidence/check-runs-before.json"

while IFS= read -r context; do
  count=$(jq \
    --arg name "$context" \
    '[.check_runs[] | select(.name == $name and .status == "completed" and .conclusion == "success")] | length' \
    "$evidence/check-runs-before.json")
  if [[ "$count" -lt 1 ]]; then
    echo "refusing protection: exact main has no completed successful $context check" >&2
    exit 66
  fi
done < <(jq -r '.[]' "$evidence/required-contexts.json")

api "/repos/$repository/actions/permissions" >"$evidence/actions-permissions.json"
jq -e '.enabled == true' "$evidence/actions-permissions.json" >/dev/null || {
  echo "repository Actions are not enabled; resolve organization/repository policy first" >&2
  exit 67
}

jq -n \
  --slurpfile contexts "$evidence/required-contexts.json" \
  '{
    required_status_checks: {strict: true, contexts: $contexts[0]},
    enforce_admins: true,
    required_pull_request_reviews: {
      dismissal_restrictions: {},
      dismiss_stale_reviews: true,
      require_code_owner_reviews: true,
      required_approving_review_count: 1,
      require_last_push_approval: true,
      bypass_pull_request_allowances: {}
    },
    restrictions: null,
    required_linear_history: true,
    allow_force_pushes: false,
    allow_deletions: false,
    block_creations: false,
    required_conversation_resolution: true,
    lock_branch: false,
    allow_fork_syncing: false
  }' >"$evidence/protection-request.json"

api \
  --method PUT \
  -H 'Accept: application/vnd.github+json' \
  "/repos/$repository/branches/$branch/protection" \
  --input "$evidence/protection-request.json" \
  >"$evidence/protection-response.json"

api \
  -H 'Accept: application/vnd.github+json' \
  "/repos/$repository/branches/$branch/protection" \
  >"$evidence/protection-after.json"
api "/repos/$repository/branches/$branch" >"$evidence/branch-after.json"

jq -e \
  --slurpfile required "$evidence/required-contexts.json" \
  '.required_status_checks.strict == true and
   (($required[0] - .required_status_checks.contexts) | length) == 0 and
   .enforce_admins.enabled == true and
   .required_pull_request_reviews.dismiss_stale_reviews == true and
   .required_pull_request_reviews.require_code_owner_reviews == true and
   .required_pull_request_reviews.required_approving_review_count >= 1 and
   .required_pull_request_reviews.require_last_push_approval == true and
   .required_linear_history.enabled == true and
   .allow_force_pushes.enabled == false and
   .allow_deletions.enabled == false and
   .required_conversation_resolution.enabled == true' \
  "$evidence/protection-after.json" >/dev/null

jq -e \
  --arg sha "$TRNM_EXPECTED_MAIN" \
  '.protected == true and .commit.sha == $sha' \
  "$evidence/branch-after.json" >/dev/null

jq -n \
  --arg repository "$repository" \
  --argjson repository_id "$repository_id" \
  --arg branch "$branch" \
  --arg main_commit "$TRNM_EXPECTED_MAIN" \
  --slurpfile contexts "$evidence/required-contexts.json" \
  '{
    schema: "trillionnium.repository-governance-application.v2",
    repository: $repository,
    repository_id: $repository_id,
    branch: $branch,
    main_commit: $main_commit,
    required_checks: $contexts[0],
    assertions: {
      repository_identity_verified: true,
      actions_enabled_read_back: true,
      successful_exact_main_checks_verified: true,
      main_protected: true,
      strict_required_checks: true,
      admins_enforced: true,
      stale_approvals_dismissed: true,
      code_owner_review_required: true,
      last_push_approval_required: true,
      linear_history_required: true,
      force_push_forbidden: true,
      branch_deletion_forbidden: true,
      conversation_resolution_required: true
    },
    claims: {
      repository_governance_applied: true,
      compatibility_credit: false,
      production_ready: false,
      public_online: false
    }
  }' >"$evidence/result.json"

find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  >"$evidence/SHA256SUMS"

printf 'repository governance v2 applied and read back for %s at %s\n' \
  "$repository" "$TRNM_EXPECTED_MAIN"
