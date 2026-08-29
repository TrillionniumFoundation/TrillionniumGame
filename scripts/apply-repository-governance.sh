#!/usr/bin/env bash
set -euo pipefail

repository=TrillionniumFoundation/TrillionniumGame
repository_id=1323087470
branch=main
required_context=trillionnium-game-merge-gate
confirmation=apply-TrillionniumGame-main-governance-v1

usage() {
  cat <<'EOF'
Usage:
  TRNM_GOVERNANCE_CONFIRM=apply-TrillionniumGame-main-governance-v1 \
  TRNM_EXPECTED_MAIN=<40-char-sha> \
  bash scripts/apply-repository-governance.sh [evidence-dir]

The script refuses to configure the required check until that exact main SHA
already has a successful, completed `trillionnium-game-merge-gate` check run.
It never merges a PR, deletes a branch, changes environments, releases or
production settings.
EOF
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  usage
  exit 0
fi

evidence=${1:-run/governance/main-protection}
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
gh auth status >/dev/null

api() {
  gh api --hostname github.com "$@"
}

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

matching_count=$(jq \
  --arg name "$required_context" \
  '[.check_runs[] | select(.name == $name and .status == "completed" and .conclusion == "success")] | length' \
  "$evidence/check-runs-before.json")
if [[ "$matching_count" -lt 1 ]]; then
  echo "refusing protection: exact main has no completed successful $required_context check" >&2
  exit 66
fi

api "/repos/$repository/actions/permissions" >"$evidence/actions-permissions.json"
jq -e '.enabled == true' "$evidence/actions-permissions.json" >/dev/null || {
  echo "repository Actions are not enabled; organization/repository admin must resolve policy first" >&2
  exit 67
}

cat >"$evidence/protection-request.json" <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["$required_context"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismissal_restrictions": {},
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1,
    "require_last_push_approval": true,
    "bypass_pull_request_allowances": {}
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
JSON

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
  --arg context "$required_context" \
  '.required_status_checks.strict == true and
   (.required_status_checks.contexts | index($context)) != null and
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

cat >"$evidence/result.json" <<JSON
{
  "schema": "trillionnium.repository-governance-application.v1",
  "repository": "$repository",
  "repository_id": $repository_id,
  "branch": "$branch",
  "main_commit": "$TRNM_EXPECTED_MAIN",
  "required_check": "$required_context",
  "assertions": {
    "repository_identity_verified": true,
    "actions_enabled_read_back": true,
    "successful_exact_main_check_verified": true,
    "main_protected": true,
    "strict_required_check": true,
    "admins_enforced": true,
    "stale_approvals_dismissed": true,
    "code_owner_review_required": true,
    "last_push_approval_required": true,
    "linear_history_required": true,
    "force_push_forbidden": true,
    "branch_deletion_forbidden": true,
    "conversation_resolution_required": true
  },
  "claims": {
    "repository_governance_applied": true,
    "compatibility_credit": false,
    "production_ready": false,
    "public_online": false
  }
}
JSON

find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  >"$evidence/SHA256SUMS"

printf 'repository governance applied and read back for %s at %s\n' \
  "$repository" "$TRNM_EXPECTED_MAIN"
