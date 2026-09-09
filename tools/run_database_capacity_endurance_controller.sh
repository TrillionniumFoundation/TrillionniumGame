#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/durability-state-model-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/database-capacity-endurance-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/capacity-base"
ORIGINAL_TARGET="$RUNNER_TEMP/capacity-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'Database capacity/endurance controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n=== PostgreSQL logs ===\n'; docker logs trnm-capacity-pg 2>/dev/null | tail -n 160 || true
    printf '\n=== CockroachDB logs ===\n'; docker logs trnm-capacity-crdb 2>/dev/null | tail -n 160 || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/capacity-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/48/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/capacity-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 280); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/durability-state-model.py" 2>/dev/null; then
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

python3 -m py_compile "$CONTROLLER/tools/compose_database_capacity_endurance.py"
python3 "$CONTROLLER/tools/compose_database_capacity_endurance.py" "$TARGET"
git -C "$TARGET" diff --check
cd "$TARGET"
python3 scripts/check-database-capacity-endurance.py
python3 scripts/check-durability-state-model.py
python3 scripts/check-remote-mac-provider.py
python3 scripts/check-schema-authority.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
bash -n scripts/ci-database-capacity-smoke.sh
bash -n scripts/ci-database-endurance-segment.sh
git add -A
git diff --cached --check
candidate_tree=$(git write-tree)

readarray -t images < <(python3 - <<'PY'
import re
from pathlib import Path
roots=[Path('.github/workflows'),Path('docs/evidence'),Path('docs/status')]
text='\n'.join(p.read_text(encoding='utf-8',errors='ignore') for root in roots if root.exists() for p in root.rglob('*') if p.is_file())
def choose(pattern,fallback):
    values=sorted(set(re.findall(pattern,text,flags=re.I))); print(values[0] if values else fallback)
choose(r'[A-Za-z0-9._/-]*postgres[A-Za-z0-9._:@/-]*@sha256:[0-9a-f]{64}','postgres:16.4')
choose(r'[A-Za-z0-9._/-]*cockroach[A-Za-z0-9._:@/-]*@sha256:[0-9a-f]{64}','cockroachdb/cockroach:v23.2.5')
PY
)
POSTGRES_IMAGE=${images[0]}
COCKROACH_IMAGE=${images[1]}
PGBENCH_IMAGE=$POSTGRES_IMAGE
export PGBENCH_IMAGE TRNM_REQUIRE_LIVE_DATABASE=1 CANDIDATE_COMMIT="$base_head" CANDIDATE_TREE="$candidate_tree"

docker rm -f trnm-capacity-pg trnm-capacity-crdb >/dev/null 2>&1 || :
docker pull "$POSTGRES_IMAGE"
docker run -d --name trnm-capacity-pg -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm \
  -e POSTGRES_DB=trnm -p 55440:5432 "$POSTGRES_IMAGE" >/dev/null
for _ in $(seq 1 90); do docker exec trnm-capacity-pg pg_isready -U trnm -d trnm >/dev/null 2>&1 && break; sleep 1; done
docker exec trnm-capacity-pg pg_isready -U trnm -d trnm >/dev/null
mapfile -t pg_migrations < <(find migrations/postgresql -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#pg_migrations[@]}" -gt 0
cat "${pg_migrations[@]}" | docker exec -i trnm-capacity-pg psql -v ON_ERROR_STOP=1 -U trnm -d trnm >/dev/null
export TRNM_DATABASE_PROFILE=postgresql
export TRNM_DATABASE_URL='postgresql://trnm:trnm@127.0.0.1:55440/trnm'
export DURATION_SECONDS=20 CLIENTS=4 THREADS=2
export EVIDENCE_DIR="$RUNNER_TEMP/capacity-postgresql"
rm -rf "$EVIDENCE_DIR"
bash scripts/ci-database-capacity-smoke.sh | tee "$RUNNER_TEMP/capacity-postgresql.log"
docker rm -f trnm-capacity-pg >/dev/null

export EVIDENCE_DIR="$RUNNER_TEMP/capacity-cockroachdb"
docker pull "$COCKROACH_IMAGE"
docker run -d --name trnm-capacity-crdb -p 26264:26257 -p 18094:8080 "$COCKROACH_IMAGE" \
  start-single-node --insecure --listen-addr=0.0.0.0:26257 --http-addr=0.0.0.0:8080 >/dev/null
for _ in $(seq 1 120); do docker exec trnm-capacity-crdb cockroach sql --insecure --host=127.0.0.1:26257 -e 'SELECT 1' >/dev/null 2>&1 && break; sleep 1; done
docker exec trnm-capacity-crdb cockroach sql --insecure --host=127.0.0.1:26257 -e 'CREATE DATABASE IF NOT EXISTS trnm' >/dev/null
mapfile -t crdb_migrations < <(find migrations/cockroachdb -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#crdb_migrations[@]}" -gt 0
cat "${crdb_migrations[@]}" | docker exec -i trnm-capacity-crdb cockroach sql --insecure --host=127.0.0.1:26257 --database=trnm >/dev/null
export TRNM_DATABASE_PROFILE=cockroachdb
export TRNM_DATABASE_URL='postgresql://root@127.0.0.1:26264/trnm?sslmode=disable'
rm -rf "$EVIDENCE_DIR"
bash scripts/ci-database-capacity-smoke.sh | tee "$RUNNER_TEMP/capacity-cockroachdb.log"
docker rm -f trnm-capacity-crdb >/dev/null

pg_tps=$(jq -r '.transactions_per_second' "$RUNNER_TEMP/capacity-postgresql/manifest.json")
pg_latency=$(jq -r '.latency_average_ms' "$RUNNER_TEMP/capacity-postgresql/manifest.json")
crdb_tps=$(jq -r '.transactions_per_second' "$RUNNER_TEMP/capacity-cockroachdb/manifest.json")
crdb_latency=$(jq -r '.latency_average_ms' "$RUNNER_TEMP/capacity-cockroachdb/manifest.json")

remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'operations: add dual-profile capacity and endurance evidence tooling'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
python3 "$CONTROLLER/tools/compose_database_capacity_endurance.py" "$TARGET"
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft operations increment is stacked on the exhaustive durability model.

\`\`\`text
base_branch      ${BASE_BRANCH}
base_commit      ${base_head}
head_branch      ${TARGET_BRANCH}
head_commit      ${head}
head_tree        ${tree}
controller_run   ${GITHUB_RUN_ID}
postgres_tps     ${pg_tps}
postgres_avg_ms  ${pg_latency}
cockroach_tps    ${crdb_tps}
cockroach_avg_ms ${crdb_latency}
\`\`\`

The controller ran the same transactional storage upsert/read pgbench workload against separate PostgreSQL and CockroachDB profiles with zero failed transactions. The repository now includes hash-chained endurance segments and a finalizer that rejects candidate/workload changes, segment gaps, failed transactions and totals below 24h, 72h or 7d.

These 20-second runs validate the workload and evidence machinery only. No throughput/latency target is accepted, and 24h/72h/7d endurance, independent performance/SRE review, production sizing and all-gap closure remain false.

\`\`\`text
dual_profile_capacity_smoke=true
capacity_target_accepted=false
endurance_24h_complete=false
endurance_72h_complete=false
endurance_7d_complete=false
performance_accepted=false
independently_accepted=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'operations/capacity: add dual-profile load and chained endurance evidence' --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title 'operations/capacity: add dual-profile load and chained endurance evidence' --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/48/comments" \
  -f body="Dual-profile capacity tooling passed 20-second PostgreSQL and CockroachDB smoke packets at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). Observed PostgreSQL ${pg_tps} tps/${pg_latency} ms avg and CockroachDB ${crdb_tps} tps/${crdb_latency} ms avg are measurements, not accepted targets. 24h/72h/7d endurance and independent acceptance remain false." >/dev/null
