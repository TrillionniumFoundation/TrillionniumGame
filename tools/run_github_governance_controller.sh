#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/denominator-review-packets-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/github-governance-readback-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/governance-base"
ORIGINAL_TARGET="$RUNNER_TEMP/governance-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'GitHub governance controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/governance-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/7/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/governance-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 360); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-denominator-review-packets.py" 2>/dev/null; then
      base_head=$candidate; break
    fi
  fi
  sleep 12
done
test -n "$base_head"
printf '%s\n' "$base_head" > "$BASE_HEAD_FILE"
target_head=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
printf '%s\n' "$target_head" > "$ORIGINAL_TARGET"
if [[ -n "$target_head" ]]; then
  git -C "$TARGET" fetch --no-tags origin "$target_head"
  git -C "$TARGET" checkout -B "$TARGET_BRANCH" FETCH_HEAD
  git -C "$TARGET" fetch --no-tags origin "$base_head"
  git -C "$TARGET" merge-base --is-ancestor "$base_head" HEAD
else
  git -C "$TARGET" fetch --no-tags origin "$base_head"
  git -C "$TARGET" checkout -B "$TARGET_BRANCH" FETCH_HEAD
fi

python3 -m py_compile "$CONTROLLER/tools/compose_github_governance_readback.py"
python3 "$CONTROLLER/tools/compose_github_governance_readback.py" "$TARGET"
cd "$TARGET"
python3 scripts/check-github-governance-contract.py
python3 scripts/check-denominator-review-packets.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q

mkdir -p docs/status/github-governance-readback
python3 scripts/capture-github-governance-readback.py \
  --repository "$GITHUB_REPOSITORY" \
  --output docs/status/github-governance-readback/before.json
python3 scripts/verify-github-governance-readback.py \
  --readback docs/status/github-governance-readback/before.json \
  --output docs/status/github-governance-readback/before-evaluation.json
python3 scripts/apply-github-governance-contract.py \
  --repository "$GITHUB_REPOSITORY" \
  --payload docs/governance/MAIN_BRANCH_PROTECTION_API_PAYLOAD.json \
  --output docs/status/github-governance-readback/apply-result.json
python3 scripts/capture-github-governance-readback.py \
  --repository "$GITHUB_REPOSITORY" \
  --output docs/status/github-governance-readback/after.json
python3 scripts/verify-github-governance-readback.py \
  --readback docs/status/github-governance-readback/after.json \
  --output docs/status/github-governance-readback/after-evaluation.json

git add -A
git diff --cached --check
remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'governance: retain administration apply and exact readback packet'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
apply_status=$(jq -r '.http_status' docs/status/github-governance-readback/apply-result.json)
apply_succeeded=$(jq -r '.succeeded' docs/status/github-governance-readback/apply-result.json)
passed=$(jq -r '.passed' docs/status/github-governance-readback/after-evaluation.json)
failed=$(jq -r '.failed' docs/status/github-governance-readback/after-evaluation.json)
all_pass=$(jq -r '.all_required_pass' docs/status/github-governance-readback/after-evaluation.json)
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft governance increment is stacked on the leaf-complete denominator review packet candidate.

\`\`\`text
base_branch        ${BASE_BRANCH}
base_commit        ${base_head}
head_branch        ${TARGET_BRANCH}
head_commit        ${head}
head_tree          ${tree}
controller_run     ${GITHUB_RUN_ID}
admin_apply_http   ${apply_status}
admin_apply_success ${apply_succeeded}
requirements_passed ${passed}
requirements_failed ${failed}
all_required_pass  ${all_pass}
\`\`\`

The controller captured branch protection, repository rulesets, Actions permissions, workflow-token policy, environments and PR #63; attempted the authorized exact branch-protection hardening; then captured and evaluated the state again. Successful and failed endpoint bodies are retained by digest.

Even when repository settings pass, the negative no-bypass rehearsal and independent governance acceptance remain separate facts. If the installed token received HTTP 403, the exact administrator payload and commands are retained and the gap remains externally blocked.

\`\`\`text
governance_readback_all_required_pass=${all_pass}
admin_apply_success=${apply_succeeded}
negative_merge_rehearsal_accepted=false
independent_governance_acceptance=false
accepted_evidence=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'governance: apply and retain exact GitHub no-bypass readback' --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title 'governance: apply and retain exact GitHub no-bypass readback' --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/7/comments" \
  -f body="GitHub governance apply/readback packet published at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). Apply HTTP=${apply_status}, succeeded=${apply_succeeded}; requirements passed=${passed}, failed=${failed}, all_required_pass=${all_pass}. Negative no-bypass rehearsal and independent governance acceptance remain false." >/dev/null
