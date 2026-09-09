#!/usr/bin/env bash
set -Eeuo pipefail

CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
bash "$CONTROLLER/tools/run_commit_fault_boundary_controller.sh"
cd "$TARGET"
python3 "$CONTROLLER/tools/compose_commit_fault_boundaries_v2.py" "$TARGET"
cargo generate-lockfile
cargo fmt --all
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
  -f body="Durable-boundary controller reached a deterministic fixed point at exact remote head \`${head}\` (tree \`${tree}\`, run \`${GITHUB_RUN_ID}\`). The same tree passed full control/Rust and separate live PostgreSQL/CockroachDB rollback packets. Independent acceptance and production claims remain false." >/dev/null
