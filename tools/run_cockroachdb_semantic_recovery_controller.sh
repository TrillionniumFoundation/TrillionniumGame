#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/postgresql-semantic-recovery-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/cockroachdb-semantic-recovery-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/crdb-semantic-base"
ORIGINAL_TARGET="$RUNNER_TEMP/crdb-semantic-original-target"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'CockroachDB semantic recovery controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'
    git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n=== CockroachDB logs ===\n'
    docker logs trnm-semantic-recovery-crdb 2>/dev/null | tail -n 180 || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/crdb-semantic-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/crdb-semantic-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin \
  "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

base_head=''
for _ in $(seq 1 120); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-postgresql-semantic-recovery.py" 2>/dev/null; then
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

python3 -m py_compile \
  "$CONTROLLER/tools/compose_postgresql_semantic_recovery.py" \
  "$CONTROLLER/tools/compose_cockroachdb_semantic_recovery.py"
python3 "$CONTROLLER/tools/compose_cockroachdb_semantic_recovery.py" "$TARGET"
git -C "$TARGET" diff --check

cd "$TARGET"
python3 scripts/check-cockroachdb-semantic-recovery.py
python3 scripts/check-postgresql-semantic-recovery.py
python3 scripts/check-schema-authority.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
bash -n scripts/ci-cockroachdb-semantic-recovery.sh
git add -A
git diff --cached --check
candidate_tree=$(git write-tree)

COCKROACH_IMAGE=$(python3 - <<'PY'
import re
from pathlib import Path
roots=[Path('.github/workflows'), Path('docs/evidence'), Path('docs/status')]
text='\n'.join(
    p.read_text(encoding='utf-8', errors='ignore')
    for root in roots if root.exists()
    for p in root.rglob('*') if p.is_file()
)
values=sorted(set(re.findall(
    r'[A-Za-z0-9._/-]*cockroach[A-Za-z0-9._:@/-]*@sha256:[0-9a-f]{64}',
    text,
    flags=re.I,
)))
print(values[0] if values else 'cockroachdb/cockroach:v23.2.5')
PY
)
export COCKROACH_IMAGE
export EVIDENCE_DIR="$RUNNER_TEMP/cockroachdb-semantic-recovery"
rm -rf "$EVIDENCE_DIR"
bash scripts/ci-cockroachdb-semantic-recovery.sh | tee "$RUNNER_TEMP/crdb-semantic-run.log"
python3 - "$EVIDENCE_DIR/manifest.json" "$base_head" "$candidate_tree" "$GITHUB_RUN_ID" <<'PY'
import json
import sys
from pathlib import Path
path=Path(sys.argv[1])
value=json.loads(path.read_text(encoding='utf-8'))
value['base_commit']=sys.argv[2]
value['candidate_tree']=sys.argv[3]
value['controller_run_id']=sys.argv[4]
path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps(value,sort_keys=True))
PY

remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
original_target=$(cat "$ORIGINAL_TARGET")
test "$remote_target" = "$original_target"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'database: prove CockroachDB native semantic backup and restore'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi

python3 "$CONTROLLER/tools/compose_cockroachdb_semantic_recovery.py" "$TARGET"
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
manifest=$(cat "$EVIDENCE_DIR/manifest.json")
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
  --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft AC-3 recovery increment is a separate CockroachDB profile stacked on the PostgreSQL semantic-recovery candidate.

\`\`\`text
base_branch    ${BASE_BRANCH}
base_commit    ${base_head}
head_branch    ${TARGET_BRANCH}
head_commit    ${head}
head_tree      ${tree}
controller_run ${GITHUB_RUN_ID}
\`\`\`

The controller applied only the authoritative CockroachDB migrations, proved repeat migration rejection without catalog drift, executed ten negative constraint probes, used CockroachDB native \`BACKUP DATABASE\` and \`RESTORE DATABASE\`, and required exact equality of canonical data and normalized \`SHOW CREATE ALL TABLES\` output. Image, migration-lock and backup-tree identities are retained in the run manifest.

No PostgreSQL evidence was inherited. Node/leaseholder failover, approved RPO/RTO, independent acceptance, production promotion and all-gap closure remain false.

\`\`\`text
cockroachdb_semantic_restore_source_candidate=true
controller_qualified=true
exact_head_native_complete=false
node_failover_proven=false
leaseholder_failover_proven=false
approved_rpo_rto=false
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
    --title 'database/recovery: prove CockroachDB native semantic backup and restore' \
    --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" \
    --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" \
    --title 'database/recovery: prove CockroachDB native semantic backup and restore' \
    --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
  -f body="CockroachDB native semantic backup/restore packet passed at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, Draft PR #${number}). It proves this single-node recovery run only; node/leaseholder failover, approved RPO/RTO, independent acceptance and production claims remain false.\n\n\`\`\`json\n${manifest}\n\`\`\`" >/dev/null
