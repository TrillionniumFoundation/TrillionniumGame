#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
: "${PR_NUMBER:?}"
: "${EXPECTED_WORKFLOW_COUNT:=56}"

pr_json="$RUNNER_TEMP/pr-${PR_NUMBER}.json"
runs_json="$RUNNER_TEMP/pr-${PR_NUMBER}-runs.json"
gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$pr_json"
head=$(python3 - "$pr_json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['head']['sha'])
PY
)
head_branch=$(python3 - "$pr_json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['head']['ref'])
PY
)
base=$(python3 - "$pr_json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['base']['sha'])
PY
)

for _ in $(seq 1 120); do
  gh api "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head}&event=pull_request&per_page=100" > "$runs_json"
  state=$(python3 - "$runs_json" "$EXPECTED_WORKFLOW_COUNT" <<'PY'
import json,sys
value=json.load(open(sys.argv[1]))
expected=int(sys.argv[2])
runs=value.get('workflow_runs',[])
if len(runs) < expected:
    print('pending')
elif any(r.get('status') != 'completed' for r in runs):
    print('pending')
elif any(r.get('conclusion') != 'success' for r in runs):
    print('failed')
else:
    print('success')
PY
  )
  case "$state" in
    success) break ;;
    failed)
      python3 - "$runs_json" <<'PY' > "$RUNNER_TEMP/pr-workflow-failures.txt"
import json,sys
runs=json.load(open(sys.argv[1])).get('workflow_runs',[])
for row in sorted(runs,key=lambda r:r.get('name','')):
    if row.get('status')!='completed' or row.get('conclusion')!='success':
        print(f"{row.get('name')} id={row.get('id')} status={row.get('status')} conclusion={row.get('conclusion')}")
PY
      gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
        -f body="Exact-head qualification failed closed for \`${head}\`.\n\n\`\`\`text\n$(cat "$RUNNER_TEMP/pr-workflow-failures.txt")\n\`\`\`" >/dev/null
      exit 1
      ;;
    pending) sleep 15 ;;
    *) exit 2 ;;
  esac
done
test "$state" = success

current_head=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')
test "$current_head" = "$head"
count=$(python3 - "$runs_json" <<'PY'
import json,sys
print(len(json.load(open(sys.argv[1])).get('workflow_runs',[])))
PY
)
aggregate=$(python3 - "$runs_json" <<'PY'
import json,sys
runs=json.load(open(sys.argv[1])).get('workflow_runs',[])
rows=[r for r in runs if r.get('name')=='trillionnium-game-merge-gate']
print(rows[0]['id'] if rows else '')
PY
)
prospective=$(python3 - "$runs_json" <<'PY'
import json,sys
runs=json.load(open(sys.argv[1])).get('workflow_runs',[])
rows=[r for r in runs if r.get('name')=='prospective-merge-gate']
print(rows[0]['id'] if rows else '')
PY
)
test -n "$aggregate"
test -n "$prospective"
merge=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.merge_commit_sha')
test -n "$merge"
merge_tree=$(gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${merge}" --jq '.tree.sha')
head_tree=$(gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${head}" --jq '.tree.sha')
body_file="$RUNNER_TEMP/pr-${PR_NUMBER}-body.md"
gh pr view "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --json body --jq '.body' > "$body_file"
python3 - "$body_file" "$head" "$head_tree" "$base" "$merge" "$merge_tree" "$count" "$aggregate" "$prospective" <<'PY'
import re,sys
from pathlib import Path
path=Path(sys.argv[1])
body=path.read_text(encoding='utf-8')
for old,new in [
    ('exact_head_native_complete=false','exact_head_native_complete=true'),
    ('exact_head_native_workflows_complete=false','exact_head_native_workflows_complete=true'),
    ('remote_verified=false','remote_verified=true'),
]:
    body=body.replace(old,new)
body=re.sub(r'\n## Exact-head workflow qualification\n.*?\n## ', '\n## ', body, flags=re.S)
section=f'''\n\n## Exact-head workflow qualification

```text
source_head             {sys.argv[2]}
source_tree             {sys.argv[3]}
base_commit             {sys.argv[4]}
prospective_merge       {sys.argv[5]}
prospective_merge_tree  {sys.argv[6]}
native_workflows        {sys.argv[7]}/{sys.argv[7]} terminal success
aggregate_run           {sys.argv[8]}
prospective_merge_run   {sys.argv[9]}
```

This promotes only exact-head and prospective-merge execution to a remote-verified candidate. It does not create independent acceptance, production durability, PITR/HA, approved RPO/RTO, complete Nakama parity, cutover authority or all-gap closure.
'''
body=body.rstrip()+section
path.write_text(body,encoding='utf-8')
PY
gh pr edit "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body-file "$body_file"
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
  -f body="Exact-head qualification passed for \`${head}\` (tree \`${head_tree}\`): ${count}/${count} pull-request workflows terminal success, aggregate run \`${aggregate}\`, prospective-merge run \`${prospective}\`, merge \`${merge}\` (tree \`${merge_tree}\`). Independent acceptance and all-gap closure remain false." >/dev/null
printf 'qualified pr=%s branch=%s head=%s tree=%s workflows=%s aggregate=%s prospective=%s merge=%s merge_tree=%s\n' \
  "$PR_NUMBER" "$head_branch" "$head" "$head_tree" "$count" "$aggregate" "$prospective" "$merge" "$merge_tree"
