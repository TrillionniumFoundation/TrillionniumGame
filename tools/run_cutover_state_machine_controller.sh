#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/remote-mac-unix-transport-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/cutover-state-machine-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/cutover-base"
ORIGINAL_TARGET="$RUNNER_TEMP/cutover-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'Cutover state-machine controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'
    git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n=== blocker packet ===\n'
    cat "$RUNNER_TEMP/cutover-blockers.json" 2>/dev/null || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/cutover-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/7/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/cutover-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin \
  "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

base_head=''
for _ in $(seq 1 440); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-remote-mac-unix-transport.py" 2>/dev/null; then
      base_head=$candidate
      break
    fi
  fi
  sleep 12
done
test -n "$base_head"
printf '%s\n' "$base_head" > "$BASE_HEAD_FILE"
base_tree=$(gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${base_head}" --jq '.tree.sha')

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

python3 -m py_compile "$CONTROLLER/tools/compose_cutover_state_machine.py"
python3 "$CONTROLLER/tools/compose_cutover_state_machine.py" "$TARGET"
git -C "$TARGET" diff --check

cd "$TARGET"
rustup toolchain install 1.85.1 --profile minimal --component rustfmt --component clippy
rustup override set 1.85.1
cargo generate-lockfile
cargo fmt --all
cargo fmt --all -- --check
python3 scripts/check-cutover-state-machine.py
python3 scripts/check-remote-mac-unix-transport.py
python3 scripts/check-remote-mac-provider.py
python3 scripts/check-github-governance-contract.py
python3 scripts/check-denominator-review-packets.py
python3 scripts/check-database-capacity-endurance.py
python3 scripts/check-durability-state-model.py
python3 scripts/check-schema-authority.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-rust-foundation.py
python3 scripts/check-rust-package-inventory.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings

git add -A
git diff --cached --check
candidate_tree=$(git write-tree)
cat > "$RUNNER_TEMP/cutover-binding.json" <<EOF
{
  "repository": "${GITHUB_REPOSITORY}",
  "source_head": "${base_head}",
  "source_tree": "${base_tree}",
  "prospective_merge": "${base_head}",
  "prospective_merge_tree": "${base_tree}"
}
EOF
python3 scripts/derive-cutover-blocker-packet.py \
  --gap-register docs/status/GAP_REGISTER.json \
  --binding "$RUNNER_TEMP/cutover-binding.json" \
  --output "$RUNNER_TEMP/cutover-blockers.json"
open_p0_p1=$(jq -r '.open_p0_p1_count' "$RUNNER_TEMP/cutover-blockers.json")
test "$open_p0_p1" -gt 0
state=$(jq -r '.state' docs/status/CUTOVER_STATE.json)
test "$state" = planning
for claim in public_online cutover_authorized nakama_retired; do
  test "$(jq -r --arg claim "$claim" '.claims[$claim]' docs/status/CUTOVER_STATE.json)" = false
done

remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'operations: add fail-closed cutover and retirement state machine'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi

python3 "$CONTROLLER/tools/compose_cutover_state_machine.py" "$TARGET"
cargo generate-lockfile
cargo fmt --all
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
  --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft migration-governance increment is stacked on the complete repository-controlled database, security, denominator and GitHub-governance source candidate.

\`\`\`text
base_branch       ${BASE_BRANCH}
base_commit       ${base_head}
head_branch       ${TARGET_BRANCH}
head_commit       ${head}
head_tree         ${tree}
controller_run    ${GITHUB_RUN_ID}
current_state     planning
open_p0_p1_count  ${open_p0_p1}
\`\`\`

The state machine permits only planning → shadow → exclusive-canary → production → retirement-pending → retired. Every edge requires exact source/merge identity, zero open P0/P1 gaps, accepted evidence, independent review, governance read-back, ordinary protected admission and an accepted rollback packet. Production additionally requires accepted 24h/72h/7d endurance and approved RPO/RTO. Retirement additionally requires complete Nakama compatibility, global SG1 and a distinct explicit retirement decision after the rollback window.

The live blocker packet derived from the current gap register contains ${open_p0_p1} open P0/P1 rows, so the state remains planning. No transition, deployment, cutover or retirement has been authorized.

\`\`\`text
cutover_state_machine_source_candidate=true
current_state=planning
shadow_authorized=false
exclusive_canary_authorized=false
production_authorized=false
public_online=false
cutover_authorized=false
nakama_retired=false
accepted_evidence=false
independently_accepted=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft \
    --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'operations/cutover: enforce shadow, canary, production and retirement gates' \
    --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
    --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" \
    --title 'operations/cutover: enforce shadow, canary, production and retirement gates' \
    --body "$body"
fi
trap - ERR
for issue in 7 36; do
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${issue}/comments" \
    -f body="Cutover/retirement state-machine candidate passed full control/Python and Rust all-target strict Clippy at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). The current gap-derived blocker packet contains ${open_p0_p1} open P0/P1 rows, so the state remains \`planning\`; no shadow, canary, production, cutover or retirement transition is authorized." >/dev/null
done
