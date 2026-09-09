#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
ROOT=${ROOT:-$GITHUB_WORKSPACE/controller}
BRANCH=${BRANCH:-codex/plan-v32-controller-v2-2026-09-09}
OUTPUT="$ROOT/docs/status/PLAN_V32_CLOSURE_LEDGER_V3.json"
WORK="$RUNNER_TEMP/plan-v32-closure-ledger-v3"
OWNER=${GITHUB_REPOSITORY%%/*}
rm -rf "$WORK"
mkdir -p "$WORK"

branches=(
  codex/postgresql-authority-storage-closure-2026-09-09
  codex/postgresql-fault-recovery-closure-2026-09-09
  codex/postgresql-semantic-recovery-closure-2026-09-09
  codex/cockroachdb-semantic-recovery-closure-2026-09-09
  codex/postgresql-recovery-barrier-closure-2026-09-09
  codex/postgresql-connection-fault-closure-2026-09-09
  codex/postgresql-primary-failover-closure-2026-09-09
  codex/cockroachdb-node-failover-closure-2026-09-09
  codex/postgresql-pitr-closure-2026-09-09
  codex/remote-mac-provider-closure-2026-09-09
  codex/durability-state-model-closure-2026-09-09
  codex/database-capacity-endurance-closure-2026-09-09
  codex/denominator-review-packets-closure-2026-09-09
  codex/github-governance-readback-closure-2026-09-09
  codex/remote-mac-unix-transport-closure-2026-09-09
  codex/cutover-state-machine-closure-2026-09-09
  codex/plan-v32-external-blocker-handoff-2026-09-09
)

for branch in "${branches[@]}"; do
  key=${branch//\//__}
  head=$(git -C "$ROOT" ls-remote origin "refs/heads/${branch}" | awk '{print $1}')
  printf '%s\n' "$head" > "$WORK/${key}.head"
  [[ -n "$head" ]] || continue
  gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${head}" > "$WORK/${key}.commit.json"
  gh api --method GET "/repos/${GITHUB_REPOSITORY}/pulls" \
    -f state=open -f head="${OWNER}:${branch}" -f per_page=100 \
    > "$WORK/${key}.prs.json"
  pr=$(jq -r '.[0].number // empty' "$WORK/${key}.prs.json")
  [[ -n "$pr" ]] || continue
  gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr}" > "$WORK/${key}.pr.json"
  merge=$(jq -r '.merge_commit_sha // empty' "$WORK/${key}.pr.json")
  if [[ -n "$merge" ]]; then
    gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${merge}" > "$WORK/${key}.merge.json" || :
  fi
  gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr}/reviews?per_page=100" \
    > "$WORK/${key}.reviews.json"
  gh api "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head}&event=pull_request&per_page=100" \
    > "$WORK/${key}.runs.json"
done

tip_branch=''
for ((index=${#branches[@]}-1; index>=0; index--)); do
  branch=${branches[$index]}
  key=${branch//\//__}
  if [[ -s "$WORK/${key}.head" ]] && [[ -n "$(cat "$WORK/${key}.head")" ]]; then
    tip_branch=$branch
    break
  fi
done

fetch_content() {
  local path=$1 output=$2
  local encoded
  if encoded=$(gh api "/repos/${GITHUB_REPOSITORY}/contents/${path}?ref=${tip_branch}" --jq '.content' 2>/dev/null); then
    printf '%s' "$encoded" | tr -d '\n' | base64 -d > "$output"
  fi
}
if [[ -n "$tip_branch" ]]; then
  fetch_content docs/status/GAP_REGISTER.json "$WORK/gap-register.json"
  fetch_content docs/status/github-governance-readback/after-evaluation.json "$WORK/governance.json"
  fetch_content docs/review/denominator-family-packets/index.json "$WORK/denominator.json"
  fetch_content docs/status/CUTOVER_STATE.json "$WORK/cutover.json"
  fetch_content docs/status/PLAN_V32_EXTERNAL_BLOCKERS.json "$WORK/external-blockers.json"
fi

python3 - "$WORK" "$OUTPUT" "$tip_branch" "${branches[@]}" <<'PY'
from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path

work=Path(sys.argv[1]); output=Path(sys.argv[2]); tip_branch=sys.argv[3] or None; branches=sys.argv[4:]
layers=[]
for branch in branches:
    key=branch.replace('/','__')
    head_path=work/f'{key}.head'
    head=head_path.read_text().strip() if head_path.exists() else ''
    row={
        'branch':branch,
        'materialized':bool(head),
        'head_commit':head or None,
        'head_tree':None,
        'pull_request':None,
        'base_branch':None,
        'base_commit':None,
        'draft':None,
        'mergeable':None,
        'mergeable_state':None,
        'prospective_merge_commit':None,
        'prospective_merge_tree':None,
        'latest_native_workflow_count':0,
        'latest_native_workflows_terminal_success':False,
        'workflow_failures_or_pending':[],
        'aggregate_run_id':None,
        'prospective_merge_run_id':None,
        'requested_reviewers':[],
        'requested_teams':[],
        'independent_approvals':[],
        'repository_controlled_qualified':False,
        'independently_accepted':False,
    }
    commit_path=work/f'{key}.commit.json'
    if commit_path.exists():
        row['head_tree']=json.loads(commit_path.read_text()).get('tree',{}).get('sha')
    prs_path=work/f'{key}.prs.json'
    if prs_path.exists():
        prs=json.loads(prs_path.read_text())
        if prs:
            pr=json.loads((work/f'{key}.pr.json').read_text())
            prn=pr['number']
            row['pull_request']={'number':prn,'title':pr.get('title'),'url':pr.get('html_url')}
            row['base_branch']=pr.get('base',{}).get('ref')
            row['base_commit']=pr.get('base',{}).get('sha')
            row['draft']=pr.get('draft')
            row['mergeable']=pr.get('mergeable')
            row['mergeable_state']=pr.get('mergeable_state')
            row['prospective_merge_commit']=pr.get('merge_commit_sha')
            merge_path=work/f'{key}.merge.json'
            if merge_path.exists():
                row['prospective_merge_tree']=json.loads(merge_path.read_text()).get('tree',{}).get('sha')
            row['requested_reviewers']=sorted(r['login'] for r in pr.get('requested_reviewers',[]) if r.get('login'))
            row['requested_teams']=sorted(r['slug'] for r in pr.get('requested_teams',[]) if r.get('slug'))
            author=pr.get('user',{}).get('login')
            reviews=json.loads((work/f'{key}.reviews.json').read_text())
            latest_review={}
            for review in sorted(reviews,key=lambda r:r.get('submitted_at') or ''):
                login=review.get('user',{}).get('login')
                if login:
                    latest_review[login]=review
            row['independent_approvals']=sorted(
                login for login,review in latest_review.items()
                if login != author and not login.endswith('[bot]') and review.get('state') == 'APPROVED'
            )
            runs=json.loads((work/f'{key}.runs.json').read_text()).get('workflow_runs',[])
            latest={}
            for run in runs:
                name=run.get('name') or ''
                if name and (name not in latest or (run.get('created_at') or '') > (latest[name].get('created_at') or '')):
                    latest[name]=run
            row['latest_native_workflow_count']=len(latest)
            row['latest_native_workflows_terminal_success']=(
                len(latest) >= 56 and
                all(run.get('status') == 'completed' and run.get('conclusion') == 'success' for run in latest.values())
            )
            row['workflow_failures_or_pending']=[
                {'name':name,'run_id':run.get('id'),'status':run.get('status'),'conclusion':run.get('conclusion')}
                for name,run in sorted(latest.items())
                if run.get('status') != 'completed' or run.get('conclusion') != 'success'
            ]
            row['aggregate_run_id']=latest.get('trillionnium-game-merge-gate',{}).get('id')
            row['prospective_merge_run_id']=latest.get('prospective-merge-gate',{}).get('id')
            row['repository_controlled_qualified']=(
                row['latest_native_workflows_terminal_success'] and
                row['aggregate_run_id'] is not None and
                row['prospective_merge_run_id'] is not None and
                row['prospective_merge_commit'] is not None and
                row['prospective_merge_tree'] is not None and
                row['head_commit'] == pr.get('head',{}).get('sha')
            )
            row['independently_accepted']=bool(row['independent_approvals'])
    layers.append(row)

closed={'closed','rejected','superseded'}
gaps=[]
gap_path=work/'gap-register.json'
if gap_path.exists():
    value=json.loads(gap_path.read_text())
    gaps=[
        {'id':r.get('id'),'severity':r.get('severity'),'status':r.get('status'),'owner_role':r.get('owner_role'),'external_dependency':r.get('external_dependency')}
        for r in value.get('gaps',[])
        if r.get('severity') in {'P0','P1'} and r.get('status') not in closed
    ]
    gaps.sort(key=lambda r:(r.get('severity') or '',r.get('id') or ''))

def optional(name):
    path=work/name
    return json.loads(path.read_text()) if path.exists() else None

governance=optional('governance.json')
denominator=optional('denominator.json')
cutover=optional('cutover.json')
external=optional('external-blockers.json')
summary={
    'expected_layers':len(layers),
    'materialized_layers':sum(r['materialized'] for r in layers),
    'open_pull_requests':sum(r['pull_request'] is not None for r in layers),
    'repository_controlled_qualified_layers':sum(r['repository_controlled_qualified'] for r in layers),
    'independently_accepted_layers':sum(r['independently_accepted'] for r in layers),
    'open_p0_p1_count':len(gaps),
    'governance_all_required_pass':bool(governance and governance.get('all_required_pass')),
    'denominator_family_count':denominator.get('family_count') if denominator else None,
    'denominator_leaf_count':denominator.get('leaf_count') if denominator else None,
    'cutover_state':cutover.get('state') if cutover else None,
    'external_blocker_count':len(external.get('blockers',[])) if external else None,
}
all_repository=(summary['materialized_layers']==len(layers) and summary['repository_controlled_qualified_layers']==len(layers))
value={
    'schema':'trillionnium.plan-v32-closure-ledger.v3',
    'repository':'TrillionniumFoundation/TrillionniumGame',
    'generated_at':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    'tip_branch_examined':tip_branch,
    'summary':summary,
    'layers':layers,
    'open_p0_p1':gaps,
    'governance_evaluation':governance,
    'denominator_review_index_summary':None if denominator is None else {'family_count':denominator.get('family_count'),'leaf_count':denominator.get('leaf_count'),'claim_boundary':denominator.get('claim_boundary')},
    'cutover_state':cutover,
    'external_blocker_handoff':external,
    'claim_boundary':{
        'all_repository_controlled_layers_qualified':all_repository,
        'all_external_facts_accepted':False,
        'all_gaps_closed':False,
        'accepted_evidence':False,
        'independent_acceptance':False,
        'complete_nakama_compatibility':False,
        'production_ready':False,
        'public_online':False,
        'cutover_authorized':False,
        'nakama_retired':False,
    },
}
output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(json.dumps(summary,sort_keys=True))
PY

cd "$ROOT"
git add "$OUTPUT"
git diff --cached --check
if git diff --cached --quiet; then exit 0; fi
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
for attempt in $(seq 1 12); do
  git commit -m 'status: refresh exact REST Plan v3.2 closure ledger v3' || :
  if git push origin "HEAD:refs/heads/${BRANCH}"; then exit 0; fi
  git fetch origin "$BRANCH"
  git rebase FETCH_HEAD
  sleep $((attempt * 2))
done
exit 1
