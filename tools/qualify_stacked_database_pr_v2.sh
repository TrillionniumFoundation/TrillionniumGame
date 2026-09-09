#!/usr/bin/env bash
set -Eeuo pipefail

CONTROLLER="$GITHUB_WORKSPACE/controller"
bash "$CONTROLLER/tools/qualify_stacked_database_pr.sh"
head=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')
runs_json="$RUNNER_TEMP/pr-${PR_NUMBER}-post-edit-runs.json"
for _ in $(seq 1 80); do
  gh api "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head}&event=pull_request&per_page=100" > "$runs_json"
  state=$(python3 - "$runs_json" "$EXPECTED_WORKFLOW_COUNT" <<'PY'
import json,sys
runs=json.load(open(sys.argv[1])).get('workflow_runs',[])
expected=int(sys.argv[2])
latest={}
for row in runs:
    name=row.get('name','')
    if not name:
        continue
    if name not in latest or row.get('created_at','') > latest[name].get('created_at',''):
        latest[name]=row
if len(latest) < expected or any(r.get('status')!='completed' for r in latest.values()):
    print('pending')
elif any(r.get('conclusion')!='success' for r in latest.values()):
    print('failed')
else:
    print('success')
PY
  )
  [[ "$state" == success ]] && break
  [[ "$state" == failed ]] && exit 1
  sleep 15
done
test "$state" = success
test "$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')" = "$head"
unique=$(python3 - "$runs_json" <<'PY'
import json,sys
print(len({r.get('name') for r in json.load(open(sys.argv[1])).get('workflow_runs',[]) if r.get('name')}))
PY
)
gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
  -f body="Post-body-update fixed-point qualification passed for exact head \`${head}\`: latest run for each of ${unique} native workflow names is terminal success; no queued or in-progress pull-request run remains. Independent acceptance and all-gap closure remain false." >/dev/null
