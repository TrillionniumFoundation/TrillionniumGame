#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
TARGET_BRANCH=${TARGET_BRANCH:-codex/postgresql-fault-recovery-closure-2026-09-09}
BASE_BRANCH=${BASE_BRANCH:-codex/postgresql-authority-storage-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
ORIGINAL_FILE="$RUNNER_TEMP/commit-fault-original-head"

failure() {
  status=$?
  trap - ERR
  {
    printf 'Durable-boundary controller failed closed. No force update was attempted.\n\n```text\n'
    printf 'target_branch=%s\nbase_branch=%s\n' "$TARGET_BRANCH" "$BASE_BRANCH"
    printf 'original_head=%s\n' "$(cat "$ORIGINAL_FILE" 2>/dev/null || printf unknown)"
    printf '\n=== git status ===\n'
    git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n=== PostgreSQL logs ===\n'
    docker logs trnm-boundary-pg 2>/dev/null | tail -n 100 || true
    printf '\n=== CockroachDB logs ===\n'
    docker logs trnm-boundary-crdb 2>/dev/null | tail -n 100 || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/commit-fault-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/commit-fault-failure.md")" >/dev/null || true
  exit "$status"
}
trap failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin \
  "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
previous=''
stable=0
for _ in $(seq 1 10); do
  current=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
  test -n "$current"
  if [[ "$current" == "$previous" ]]; then stable=$((stable + 1)); else previous=$current; stable=1; fi
  if [[ "$stable" -ge 2 ]]; then break; fi
  sleep 5
done
test "$stable" -ge 2
printf '%s\n' "$previous" > "$ORIGINAL_FILE"
git -C "$TARGET" fetch --no-tags origin "$previous"
git -C "$TARGET" checkout -b "$TARGET_BRANCH" FETCH_HEAD
test "$(git -C "$TARGET" rev-parse HEAD)" = "$previous"

python3 -m py_compile \
  "$CONTROLLER/tools/compose_commit_fault_boundaries.py" \
  "$CONTROLLER/tools/compose_commit_fault_boundaries_v2.py"
python3 "$CONTROLLER/tools/compose_commit_fault_boundaries_v2.py" "$TARGET"
git -C "$TARGET" diff --check

cd "$TARGET"
rustup toolchain install 1.85.1 --profile minimal --component rustfmt --component clippy
rustup override set 1.85.1
cargo generate-lockfile
cargo fmt --all
cargo fmt --all -- --check
python3 scripts/check-schema-authority.py
python3 scripts/check-rust-foundation.py
python3 scripts/check-trnm-server.py
python3 scripts/check-rust-package-inventory.py
python3 scripts/check-documentation-authority.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
cargo test --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test -p trnm-persistence-pg --features transaction-test-hooks --all-targets --locked
cargo clippy -p trnm-persistence-pg --features transaction-test-hooks --all-targets --locked -- -D warnings

python3 - <<'PY' > "$RUNNER_TEMP/commit-fault-images"
import re
from pathlib import Path
roots=[Path('.github/workflows'), Path('docs/evidence'), Path('docs/status')]
text='\n'.join(
    p.read_text(encoding='utf-8', errors='ignore')
    for root in roots if root.exists()
    for p in root.rglob('*') if p.is_file()
)
def choose(pattern: str, fallback: str) -> None:
    values=sorted(set(re.findall(pattern, text, flags=re.I)))
    print(values[0] if values else fallback)
choose(r'[A-Za-z0-9._/-]*postgres[A-Za-z0-9._:@/-]*@sha256:[0-9a-f]{64}', 'postgres:16.4')
choose(r'[A-Za-z0-9._/-]*cockroach[A-Za-z0-9._:@/-]*@sha256:[0-9a-f]{64}', 'cockroachdb/cockroach:v23.2.5')
PY
PG_IMAGE=$(sed -n '1p' "$RUNNER_TEMP/commit-fault-images")
CRDB_IMAGE=$(sed -n '2p' "$RUNNER_TEMP/commit-fault-images")
test -n "$PG_IMAGE"
test -n "$CRDB_IMAGE"

cleanup_pg() { docker rm -f trnm-boundary-pg >/dev/null 2>&1 || true; }
cleanup_crdb() { docker rm -f trnm-boundary-crdb >/dev/null 2>&1 || true; }
cleanup_pg
cleanup_crdb

docker pull "$PG_IMAGE"
docker run -d --name trnm-boundary-pg \
  -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm \
  -p 55432:5432 "$PG_IMAGE"
for _ in $(seq 1 90); do
  docker exec trnm-boundary-pg pg_isready -U trnm -d trnm >/dev/null 2>&1 && break
  sleep 1
done
docker exec trnm-boundary-pg pg_isready -U trnm -d trnm
mapfile -t pg_migrations < <(find migrations/postgresql -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#pg_migrations[@]}" -gt 0
cat "${pg_migrations[@]}" | docker exec -i trnm-boundary-pg \
  psql -v ON_ERROR_STOP=1 -U trnm -d trnm
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_PROFILE=postgresql \
TRNM_DATABASE_URL='postgresql://trnm:trnm@127.0.0.1:55432/trnm' \
  cargo test -p trnm-persistence-pg --features transaction-test-hooks \
    --test commit_fault_boundaries --locked -- --nocapture
docker inspect --format='{{.Image}}' trnm-boundary-pg > "$RUNNER_TEMP/commit-fault-pg-id"
cleanup_pg

docker pull "$CRDB_IMAGE"
docker run -d --name trnm-boundary-crdb -p 26257:26257 -p 18080:8080 "$CRDB_IMAGE" \
  start-single-node --insecure --listen-addr=0.0.0.0:26257 --http-addr=0.0.0.0:8080
for _ in $(seq 1 120); do
  docker exec trnm-boundary-crdb cockroach sql --insecure --host=127.0.0.1:26257 \
    -e 'SELECT 1' >/dev/null 2>&1 && break
  sleep 1
done
docker exec trnm-boundary-crdb cockroach sql --insecure --host=127.0.0.1:26257 \
  -e 'CREATE DATABASE IF NOT EXISTS trnm'
mapfile -t crdb_migrations < <(find migrations/cockroachdb -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#crdb_migrations[@]}" -gt 0
cat "${crdb_migrations[@]}" | docker exec -i trnm-boundary-crdb cockroach sql \
  --insecure --host=127.0.0.1:26257 --database=trnm
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_PROFILE=cockroachdb \
TRNM_DATABASE_URL='postgresql://root@127.0.0.1:26257/trnm?sslmode=disable' \
  cargo test -p trnm-persistence-pg --features transaction-test-hooks \
    --test commit_fault_boundaries --locked -- --nocapture
docker inspect --format='{{.Image}}' trnm-boundary-crdb > "$RUNNER_TEMP/commit-fault-crdb-id"
cleanup_crdb

original=$(cat "$ORIGINAL_FILE")
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$original"
git add -A
git diff --cached --check
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'database: prove rollback at every command durable boundary'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
head=$(git rev-parse HEAD)
tree=$(git rev-parse 'HEAD^{tree}')
pg_id=$(cat "$RUNNER_TEMP/commit-fault-pg-id")
crdb_id=$(cat "$RUNNER_TEMP/commit-fault-crdb-id")
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
  --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft-only AC-3 increment is stacked on PR #121.

\`\`\`text
base_branch     ${BASE_BRANCH}
base_commit     ${original}
head_branch     ${TARGET_BRANCH}
head_commit     ${head}
head_tree       ${tree}
controller_run  ${GITHUB_RUN_ID}
postgres_image  ${PG_IMAGE}
postgres_id     ${pg_id}
cockroach_image ${CRDB_IMAGE}
cockroach_id    ${crdb_id}
\`\`\`

Five deterministic test-only mutation points cover entity-head CAS, command-receipt insertion, event append, outbox/command-outbox insertion and immediately-before-commit. Full control/Python, root Rust all-target strict Clippy and separate live PostgreSQL/CockroachDB packets passed before ordinary fast-forward publication.

Exact-head native workflows, prospective-merge qualification and independent database/data-integrity acceptance remain required. HA, PITR, approved RPO/RTO, endurance, production promotion and all-gap closure remain false.

\`\`\`text
source_candidate=true
controller_qualified=true
exact_head_native_complete=false
independently_accepted=false
ha_proven=false
pitr_proven=false
production_ready=false
all_gaps_closed=false
administrator_bypass=false
force_push=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft \
    --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'database/fault: prove rollback at every command durable boundary' \
    --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
    --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" \
    --title 'database/fault: prove rollback at every command durable boundary' \
    --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
  -f body="Five command durable boundaries passed full controller qualification and separate live PostgreSQL/CockroachDB rollback packets at source \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`). Draft PR #${number} is bound to the exact object. Independent acceptance, HA/PITR/RPO/RTO and production claims remain false." >/dev/null
