#!/usr/bin/env bash
set -Eeuo pipefail

: "${GITHUB_WORKSPACE:?}"
: "${GITHUB_REPOSITORY:?}"
: "${GH_TOKEN:?}"
BASE_BRANCH=${BASE_BRANCH:-codex/postgresql-connection-fault-closure-2026-09-09}
TARGET_BRANCH=${TARGET_BRANCH:-codex/postgresql-primary-failover-closure-2026-09-09}
CONTROLLER="$GITHUB_WORKSPACE/controller"
TARGET="$GITHUB_WORKSPACE/target"
BASE_HEAD_FILE="$RUNNER_TEMP/pg-failover-base"
ORIGINAL_TARGET="$RUNNER_TEMP/pg-failover-original"

report_failure() {
  status=$?
  trap - ERR
  {
    printf 'PostgreSQL primary-failover controller failed closed.\n\n```text\n'
    printf 'base_branch=%s\ntarget_branch=%s\n' "$BASE_BRANCH" "$TARGET_BRANCH"
    printf 'base_head=%s\n' "$(cat "$BASE_HEAD_FILE" 2>/dev/null || printf unknown)"
    printf 'original_target=%s\n' "$(cat "$ORIGINAL_TARGET" 2>/dev/null || printf absent)"
    printf '\n=== git status ===\n'; git -C "$TARGET" status --short 2>/dev/null || true
    printf '\n=== primary logs ===\n'; docker logs trnm-pg-primary 2>/dev/null | tail -n 180 || true
    printf '\n=== standby logs ===\n'; docker logs trnm-pg-standby 2>/dev/null | tail -n 180 || true
    printf '\n```\n'
  } > "$RUNNER_TEMP/pg-failover-failure.md"
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
    -f body="$(tail -c 60000 "$RUNNER_TEMP/pg-failover-failure.md")" >/dev/null || true
  exit "$status"
}
trap report_failure ERR

rm -rf "$TARGET"
git init "$TARGET"
git -C "$TARGET" remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
base_head=''
for _ in $(seq 1 180); do
  candidate=$(git -C "$CONTROLLER" ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
  if [[ -n "$candidate" ]]; then
    git -C "$TARGET" fetch --no-tags --depth=1 origin "$candidate" >/dev/null 2>&1 || true
    if git -C "$TARGET" cat-file -e "${candidate}:scripts/check-postgresql-connection-faults.py" 2>/dev/null; then
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

python3 -m py_compile "$CONTROLLER/tools/compose_postgresql_primary_failover.py"
python3 "$CONTROLLER/tools/compose_postgresql_primary_failover.py" "$TARGET"
git -C "$TARGET" diff --check
cd "$TARGET"
python3 scripts/check-postgresql-primary-failover.py
python3 scripts/check-postgresql-connection-faults.py
python3 scripts/check-postgresql-recovery-barrier.py
python3 scripts/check-postgresql-semantic-recovery.py
python3 scripts/check-cockroachdb-semantic-recovery.py
python3 scripts/check-schema-authority.py
python3 scripts/check-documentation-authority.py
python3 scripts/check-plan.py
python3 -m compileall -q scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -q
bash -n scripts/ci-postgresql-primary-failover.sh
git add -A
git diff --cached --check
candidate_tree=$(git write-tree)

POSTGRES_IMAGE=$(python3 - <<'PY'
import re
from pathlib import Path
roots=[Path('.github/workflows'),Path('docs/evidence'),Path('docs/status')]
text='\n'.join(p.read_text(encoding='utf-8',errors='ignore') for root in roots if root.exists() for p in root.rglob('*') if p.is_file())
values=sorted(set(re.findall(r'[A-Za-z0-9._/-]*postgres[A-Za-z0-9._:@/-]*@sha256:[0-9a-f]{64}',text,flags=re.I)))
print(values[0] if values else 'postgres:16.4')
PY
)
export POSTGRES_IMAGE TRNM_REQUIRE_LIVE_DATABASE=1
export EVIDENCE_DIR="$RUNNER_TEMP/postgresql-primary-failover"
rm -rf "$EVIDENCE_DIR"
bash scripts/ci-postgresql-primary-failover.sh | tee "$RUNNER_TEMP/pg-failover-run.log"
python3 - "$EVIDENCE_DIR/manifest.json" "$base_head" "$candidate_tree" "$GITHUB_RUN_ID" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); v=json.loads(p.read_text()); v['base_commit']=sys.argv[2]; v['candidate_tree']=sys.argv[3]; v['controller_run_id']=sys.argv[4]; p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n'); print(json.dumps(v,sort_keys=True))
PY

remote_base=$(git ls-remote origin "refs/heads/${BASE_BRANCH}" | awk '{print $1}')
test "$remote_base" = "$base_head"
remote_target=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote_target" = "$(cat "$ORIGINAL_TARGET")"
if ! git diff --cached --quiet; then
  git config user.name github-actions[bot]
  git config user.email 41898282+github-actions[bot]@users.noreply.github.com
  git commit -m 'database: prove synchronous PostgreSQL primary failover'
  git push origin "HEAD:refs/heads/${TARGET_BRANCH}"
else
  git diff --quiet
fi
python3 "$CONTROLLER/tools/compose_postgresql_primary_failover.py" "$TARGET"
git add -A
git diff --cached --quiet
git diff --quiet
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/${TARGET_BRANCH}" | awk '{print $1}')
test "$remote" = "$head"
tree=$(git rev-parse 'HEAD^{tree}')
manifest=$(cat "$EVIDENCE_DIR/manifest.json")
measured=$(jq -r '.measured_failover_ms' "$EVIDENCE_DIR/manifest.json")
number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number // empty')
body=$(cat <<EOF
## Exact stacked identity

This Draft AC-3 HA increment is stacked on the PostgreSQL connection-fault candidate.

\`\`\`text
base_branch         ${BASE_BRANCH}
base_commit         ${base_head}
head_branch         ${TARGET_BRANCH}
head_commit         ${head}
head_tree           ${tree}
controller_run      ${GITHUB_RUN_ID}
measured_failover_ms ${measured}
\`\`\`

A physical standby created by \`pg_basebackup\` reached streaming/synchronous state. The primary used \`synchronous_commit=remote_apply\`; the standby replay LSN covered the acknowledged commit. After the primary stopped, the standby was promoted, canonical state remained byte-identical, and a new write succeeded.

The measured interval is not an approved RTO. Automatic failback, production-grade split-brain fencing, repeated zone/region failure, independent acceptance, production readiness and all-gap closure remain false.

\`\`\`text
postgresql_primary_failover_source_candidate=true
zero_acknowledged_loss_observed=true
controller_qualified=true
exact_head_native_complete=false
approved_rpo_rto=false
split_brain_prevention_independently_accepted=false
accepted_evidence=false
independently_accepted=false
production_ready=false
all_gaps_closed=false
\`\`\`
EOF
)
if [[ -z "$number" ]]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base "$BASE_BRANCH" --head "$TARGET_BRANCH" \
    --title 'database/ha: prove synchronous PostgreSQL primary failover' --body "$body" >/dev/null
  number=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$TARGET_BRANCH" --json number --jq '.[0].number')
else
  gh pr edit "$number" --repo "$GITHUB_REPOSITORY" --title 'database/ha: prove synchronous PostgreSQL primary failover' --body "$body"
fi
trap - ERR
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/36/comments" \
  -f body="Synchronous PostgreSQL primary failover passed at \`${head}\` (tree \`${tree}\`, controller run \`${GITHUB_RUN_ID}\`, measured ${measured} ms, Draft PR #${number}). Zero acknowledged loss was observed under remote_apply for this run. Approved RPO/RTO, production fencing and independent acceptance remain false.\n\n\`\`\`json\n${manifest}\n\`\`\`" >/dev/null
