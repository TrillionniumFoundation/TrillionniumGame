#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/cutover-state-machine-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/plan-v32-external-blocker-handoff-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/external-base"
ORIGINAL_TARGET="$RUNNER_TEMP/external-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'External blocker handoff controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/external-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/7/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/external-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin \
  "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 480); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-cutover-state-machine.py" 2>/dev/null; then
      base_head=$candidate
      break
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

python3 -m py_compile "$CONTROLLER/tools/compose_external_blocker_handoff.py"
python3 "$CONTROLLER/tools/compose_external_blocker_handoff.py" "$TARGET"
git -C "$TARGET" diff --check
cd "$TARGET"
rustup toolchain install 1.85.1 --profile minimal --component rustfmt --component clippy
rustup override set 1.85.1
cargo generate-lockfile
cargo fmt --all
cargo fmt --all -- --check
python3 scripts/check-external-blocker-handoff.py
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
remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'governance: publish exact external blocker handoff'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
python3 "$CONTROLLER/tools/compose_external_blocker_handoff.py" "$TARGET"
cargo generate-lockfile
cargo fmt --all
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
blockers=$(jq -r '.blockers | length' docs/status/PLAN_V32_EXTERNAL_BLOCKERS.json)
test "$blockers" = 8
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
  --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft governance increment is the final repository-controlled handoff layer stacked on the fail-closed cutover state machine.

\`\`\`text
base_branch    ${BASE_BRANCH}
base_commit    ${base_head}
head_branch    ${TARGET_BRANCH}
head_commit    ${head}
head_tree      ${tree}
controller_run ${GITHUB_RUN_ID}
blocker_count  ${blockers}
\`\`\`

The handoff covers GitHub administration, reviewer capacity, fourteen denominator family/global SG1 decisions, vendor KMS/HSM evidence, capacity/endurance, approved RPO/RTO, complete Nakama implementation/oracle/official-SDK evidence and ordinary protected promotion/retirement. Every accepted fact must bind one exact candidate, include every required evidence type and carry a current conflict-free decision from an allowed role. Actor self-acceptance, candidate-author self-approval, evidence-producer approval, missing evidence and expired attestations are rejected.

No attestation has been synthesized. An eventual complete external-fact bundle still cannot edit the gap register, merge protected main or authorize production by itself.

\`\`\`text
external_blocker_handoff_source_candidate=true
external_blocker_count=8
all_external_facts_accepted=false
all_gaps_closed=false
accepted_evidence=false
independent_acceptance=false
complete_nakama_compatibility=false
production_ready=false
cutover_authorized=false
nakama_retired=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft \
    --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'governance/closure: publish exact external blocker handoff' \
    --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
    --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" \
    --title 'governance/closure: publish exact external blocker handoff' \
    --body "$body"
fi
trap - ERR
for issue in 7 15 21 35 36 46 48; do
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${issue}/comments" \
    -f body="Exact Plan v3.2 external-blocker handoff published at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). Eight non-synthesizable fact classes now have exact actor/reviewer/evidence/expiry contracts. No external fact has been auto-accepted; all-gap, production, cutover and retirement claims remain false." >/dev/null
done
