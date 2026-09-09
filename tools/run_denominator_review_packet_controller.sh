#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/database-capacity-endurance-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/denominator-review-packets-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/denominator-base"
ORIGINAL_TARGET="$RUNNER_TEMP/denominator-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'Denominator review-packet controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n=== packet inventory ===\n'; find "$TARGET/docs/review/denominator-family-packets" -maxdepth 1 -type f -printf '%f %s bytes\n' 2>/dev/null | sort || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/denominator-failure.md"
  for issue in 15 21 46; do
    gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${issue}/comments" \
      -f body="$(tail -c 60000 "$RUNNER_TEMP/denominator-failure.md")" >/dev/null || true
  done
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 320); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-database-capacity-endurance.py" 2>/dev/null; then
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

python3 -m py_compile "$CONTROLLER/tools/compose_denominator_review_packets.py"
python3 "$CONTROLLER/tools/compose_denominator_review_packets.py" "$TARGET"
cd "$TARGET"
python3 scripts/generate-denominator-review-packets.py
python3 scripts/check-denominator-review-packets.py | tee "$RUNNER_TEMP/denominator-report.json"
python3 scripts/check-database-capacity-endurance.py
python3 scripts/check-durability-state-model.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
git add -A
git diff --cached --check

remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'compatibility: generate leaf-complete denominator review packets'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
python3 "$CONTROLLER/tools/compose_denominator_review_packets.py" "$TARGET"
python3 scripts/generate-denominator-review-packets.py
python3 scripts/check-denominator-review-packets.py > "$RUNNER_TEMP/denominator-fixed-point.json"
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
family_count=$(jq -r '.families' "$RUNNER_TEMP/denominator-fixed-point.json")
leaf_count=$(jq -r '.leaves' "$RUNNER_TEMP/denominator-fixed-point.json")
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft compatibility-governance increment is stacked on the repository-controlled database, security, durability and capacity candidate.

\`\`\`text
base_branch    ${BASE_BRANCH}
base_commit    ${base_head}
head_branch    ${TARGET_BRANCH}
head_commit    ${head}
head_tree      ${tree}
controller_run ${GITHUB_RUN_ID}
family_count   ${family_count}
leaf_count     ${leaf_count}
\`\`\`

All fourteen pinned denominator manifests are converted into reproducible family packets containing every one of the 10,173 candidate leaves. Each row binds the source manifest, JSON collection pointer and leaf SHA-256 and starts with null classification. Family acceptance rejects missing/duplicate/hash-changed leaves, missing rationale, candidate-author self-approval and any remaining restricted/unimplemented blocker. Global SG1 requires all fourteen accepted family records plus a distinct global reviewer.

No leaf has been auto-classified and no review has been synthesized. Family decisions, legal judgments, conflict-free reviewer attestations and the separate global SG1 acceptance remain false.

\`\`\`text
denominator_review_packets_reproducible=true
family_count=14
leaf_count=10173
all_families_classified=false
all_families_accepted=false
global_sg1_accepted=false
complete_nakama_compatibility=false
accepted_evidence=false
independently_accepted=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'compatibility/review: generate 14 leaf-complete denominator packets' --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title 'compatibility/review: generate 14 leaf-complete denominator packets' --body "$body"
fi
trap - ERR
for issue in 15 21 46; do
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${issue}/comments" \
    -f body="Reproducible denominator review packets are published at \`${head}\` (tree \`${tree}\`, ${family_count} families/${leaf_count} leaves, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). Every leaf remains unclassified until a conflict-free human decision is provided; global SG1 and complete compatibility remain false." >/dev/null
done
