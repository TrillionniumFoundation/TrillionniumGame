#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/remote-mac-provider-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/durability-state-model-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/model-base"
ORIGINAL_TARGET="$RUNNER_TEMP/model-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'Durability state-model controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/model-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/model-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 260); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:crates/trnm-token-crypto-provider/src/remote.rs" 2>/dev/null; then
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

python3 -m py_compile \
  "$CONTROLLER/tools/compose_durability_state_model.py" \
  "$CONTROLLER/tools/compose_durability_state_model_v2.py"
python3 "$CONTROLLER/tools/compose_durability_state_model_v2.py" "$TARGET"
git -C "$TARGET" diff --check
cd "$TARGET"
python3 scripts/check-durability-state-model.py
python3 scripts/durability-state-model.py --depth 10 | tee "$RUNNER_TEMP/durability-model-report.json"
python3 scripts/check-remote-mac-provider.py
python3 scripts/check-schema-authority.py
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
  git commit -m 'database: add exhaustive durability and stale-writer state model'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
python3 "$CONTROLLER/tools/compose_durability_state_model_v2.py" "$TARGET"
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
states=$(jq -r '.states' "$RUNNER_TEMP/durability-model-report.json")
edges=$(jq -r '.edges' "$RUNNER_TEMP/durability-model-report.json")
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft data-integrity increment is stacked on the database and opaque remote-MAC candidate.

\`\`\`text
base_branch    ${BASE_BRANCH}
base_commit    ${base_head}
head_branch    ${TARGET_BRANCH}
head_commit    ${head}
head_tree      ${tree}
controller_run ${GITHUB_RUN_ID}
model_states   ${states}
model_edges    ${edges}
\`\`\`

The exhaustive depth-10 model covers current/stale command commit, exact replay, conflicting replay, authority takeover, claim, lease expiry, idempotent publish, exact/stale acknowledgement, crash ambiguity and final-attempt reaping. It rejects receipt/revision divergence, lost outbox intents, invalid lease ownership, stale mutation and duplicate externally visible value. Hostile stale-ack and duplicate-effect mutants must fail.

The model is a specification and vector source. Exact implementation differential, independent data-integrity acceptance, production promotion and all-gap closure remain false.

\`\`\`text
durability_state_model_source_candidate=true
hostile_mutants_rejected=true
controller_qualified=true
exact_head_native_complete=false
implementation_differential_accepted=false
accepted_evidence=false
independently_accepted=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'database/model: exhaust durability, retry and stale-writer states' --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title 'database/model: exhaust durability, retry and stale-writer states' --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
  -f body="Durability state model passed depth 10 at \`${head}\` (tree \`${tree}\`, ${states} states, ${edges} edges, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}); duplicate-effect and stale-ack mutants were rejected. Implementation differential and independent acceptance remain false." >/dev/null
