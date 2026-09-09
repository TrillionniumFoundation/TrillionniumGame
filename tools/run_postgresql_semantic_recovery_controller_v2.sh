#!/usr/bin/env bash
set -Eeuo pipefail

CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
bash "$CONTROLLER/tools/run_postgresql_semantic_recovery_controller.sh"
cd "$TARGET"
python3 "$CONTROLLER/tools/compose_postgresql_semantic_recovery_v2.py" "$TARGET"
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
  -f body="PostgreSQL semantic-recovery controller reached a deterministic fixed point at exact remote head \`${head}\` (tree \`${tree}\`, run \`${GITHUB_RUN_ID}\`). The exact tree produced equal canonical source/restored data and catalog digests. PITR, failover, approved RPO/RTO, independent acceptance and production claims remain false." >/dev/null
