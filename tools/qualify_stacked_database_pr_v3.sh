#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
: "${HEAD_BRANCH:?}"
: "${EXPECTED_WORKFLOW_COUNT:=56}"

for _ in $(seq 1 140); do
  PR_NUMBER=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$HEAD_BRANCH" \
    --json number --jq '.[0].number // empty')
  [[ -n "$PR_NUMBER" ]] && break
  sleep 12
done
test -n "$PR_NUMBER"
export PR_NUMBER

pr_json="$RUNNER_TEMP/pr-${PR_NUMBER}.json"
runs_json="$RUNNER_TEMP/pr-${PR_NUMBER}-runs.json"
gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" > "$pr_json"
head=$(python3 - "$pr_json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['head']['sha'])
PY
)
base=$(python3 - "$pr_json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['base']['sha'])
PY
)
actual_branch=$(python3 - "$pr_json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['head']['ref'])
PY
)
test "$actual_branch" = "$HEAD_BRANCH"

wait_for_latest_workflow_set() {
  local phase=$1
  local state=pending
  for _ in $(seq 1 160); do
    gh api "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head}&event=pull_request&per_page=100" > "$runs_json"
    state=$(python3 - "$runs_json" "$EXPECTED_WORKFLOW_COUNT" <<'PY'
import json,sys
runs=json.load(open(sys.argv[1])).get('workflow_runs',[])
expected=int(sys.argv[2])
latest={}
for row in runs:
    name=row.get('name') or ''
    if not name:
        continue
    if name not in latest or row.get('created_at','') > latest[name].get('created_at',''):
        latest[name]=row
if len(latest) < expected:
    print('pending')
elif any(row.get('status') != 'completed' for row in latest.values()):
    print('pending')
elif any(row.get('conclusion') != 'success' for row in latest.values()):
    print('failed')
else:
    print('success')
PY
    )
    case "$state" in
      success) break ;;
      failed)
        python3 - "$runs_json" <<'PY' > "$RUNNER_TEMP/pr-${PR_NUMBER}-${phase}-failures.txt"
import json,sys
latest={}
for row in json.load(open(sys.argv[1])).get('workflow_runs',[]):
    name=row.get('name') or ''
    if name and (name not in latest or row.get('created_at','') > latest[name].get('created_at','')):
        latest[name]=row
for name,row in sorted(latest.items()):
    if row.get('status') != 'completed' or row.get('conclusion') != 'success':
        print(f"{name} id={row.get('id')} status={row.get('status')} conclusion={row.get('conclusion')}")
PY
        gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
          -f body="${phase} exact-head qualification failed closed for \`${head}\`.\n\n\`\`\`text\n$(cat "$RUNNER_TEMP/pr-${PR_NUMBER}-${phase}-failures.txt")\n\`\`\`" >/dev/null
        return 1
        ;;
      pending) sleep 15 ;;
      *) return 2 ;;
    esac
  done
  test "$state" = success
  test "$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')" = "$head"
}

wait_for_latest_workflow_set pre-bind

for _ in $(seq 1 20); do
  merge=$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.merge_commit_sha // empty')
  [[ -n "$merge" ]] && break
  sleep 6
done
test -n "$merge"
head_tree=$(gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${head}" --jq '.tree.sha')
merge_tree=$(gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${merge}" --jq '.tree.sha')

read -r count aggregate prospective < <(python3 - "$runs_json" <<'PY'
import json,sys
latest={}
for row in json.load(open(sys.argv[1])).get('workflow_runs',[]):
    name=row.get('name') or ''
    if name and (name not in latest or row.get('created_at','') > latest[name].get('created_at','')):
        latest[name]=row
print(
    len(latest),
    latest.get('trillionnium-game-merge-gate',{}).get('id',''),
    latest.get('prospective-merge-gate',{}).get('id',''),
)
PY
)
test -n "$aggregate"
test -n "$prospective"

old_body="$RUNNER_TEMP/pr-${PR_NUMBER}-old.md"
new_body="$RUNNER_TEMP/pr-${PR_NUMBER}-new.md"
gh pr view "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --json body --jq '.body' > "$old_body"
python3 - "$old_body" "$new_body" "$head" "$head_tree" "$base" "$merge" "$merge_tree" "$count" "$aggregate" "$prospective" <<'PY'
import re,sys
from pathlib import Path
old=Path(sys.argv[1]).read_text(encoding='utf-8')
for before,after in (
    ('exact_head_native_complete=false','exact_head_native_complete=true'),
    ('exact_head_native_workflows_complete=false','exact_head_native_workflows_complete=true'),
    ('remote_verified=false','remote_verified=true'),
):
    old=old.replace(before,after)
old=re.sub(r'\n## Exact-head workflow qualification\n.*\Z','',old,flags=re.S).rstrip()
section=f'''\n\n## Exact-head workflow qualification

```text
source_head             {sys.argv[3]}
source_tree             {sys.argv[4]}
base_commit             {sys.argv[5]}
prospective_merge       {sys.argv[6]}
prospective_merge_tree  {sys.argv[7]}
native_workflows        {sys.argv[8]}/{sys.argv[8]} latest workflow names terminal success
aggregate_run           {sys.argv[9]}
prospective_merge_run   {sys.argv[10]}
```

This promotes only exact-head and prospective-merge execution to a remote-verified candidate. It does not create independent acceptance, production durability, PITR/HA, approved RPO/RTO, complete Nakama parity, cutover authority or all-gap closure.
'''
Path(sys.argv[2]).write_text(old+section,encoding='utf-8')
PY

body_changed=false
if ! cmp -s "$old_body" "$new_body"; then
  gh pr edit "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body-file "$new_body"
  body_changed=true
fi
wait_for_latest_workflow_set post-bind

test "$(gh api "/repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq '.head.sha')" = "$head"
comment="Exact-head fixed-point qualification passed for \`${head}\` (tree \`${head_tree}\`): ${count}/${count} latest native workflow names terminal success, aggregate run \`${aggregate}\`, prospective-merge run \`${prospective}\`, merge \`${merge}\` (tree \`${merge_tree}\`), body_changed=${body_changed}. Independent acceptance and all-gap closure remain false."
existing=$(gh api "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments?per_page=100" --paginate \
  --jq ".[] | select(.body == \"${comment//\"/\\\"}\") | .id" | head -n 1 || :)
if [[ -z "$existing" ]]; then
  gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments" \
    -f body="$comment" >/dev/null
fi
printf 'qualified pr=%s branch=%s head=%s tree=%s workflows=%s aggregate=%s prospective=%s merge=%s merge_tree=%s body_changed=%s\n' \
  "$PR_NUMBER" "$HEAD_BRANCH" "$head" "$head_tree" "$count" "$aggregate" "$prospective" "$merge" "$merge_tree" "$body_changed"
