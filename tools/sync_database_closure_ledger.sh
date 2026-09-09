#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
ROOT=${ROOT:-$GITHUB_WORKSPACE/controller}
OUTPUT=${OUTPUT:-$ROOT/docs/status/DATABASE_CLOSURE_LIVE_LEDGER.json}
mkdir -p "$(dirname "$OUTPUT")"

branches=(
  codex/postgresql-authority-storage-closure-2026-09-09
  codex/postgresql-fault-recovery-closure-2026-09-09
  codex/postgresql-semantic-recovery-closure-2026-09-09
  codex/cockroachdb-semantic-recovery-closure-2026-09-09
  codex/postgresql-recovery-barrier-closure-2026-09-09
)
work="$RUNNER_TEMP/database-ledger"
rm -rf "$work"
mkdir -p "$work"

for branch in "${branches[@]}"; do
  key=${branch//\//__}
  head=$(git -C "$ROOT" ls-remote origin "refs/heads/${branch}" | awk '{print $1}')
  printf '%s\n' "$head" > "$work/${key}.head"
  if [[ -z "$head" ]]; then
    continue
  fi
  gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${head}" > "$work/${key}.commit.json"
  gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$branch" \
    --json number,title,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,url \
    > "$work/${key}.prs.json"
  pr=$(jq -r '.[0].number // empty' "$work/${key}.prs.json")
  if [[ -n "$pr" ]]; then
    gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr}" > "$work/${key}.pr.json"
    gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr}/reviews?per_page=100" \
      > "$work/${key}.reviews.json"
    gh api "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head}&event=pull_request&per_page=100" \
      > "$work/${key}.runs.json"
  fi
done

python3 - "$work" "$OUTPUT" "${branches[@]}" <<'PY'
from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path

work=Path(sys.argv[1])
output=Path(sys.argv[2])
branches=sys.argv[3:]
layers=[]
for branch in branches:
    key=branch.replace('/','__')
    head=(work/f'{key}.head').read_text().strip()
    row={
        'branch':branch,
        'head_commit':head or None,
        'head_tree':None,
        'pull_request':None,
        'latest_workflow_names':0,
        'latest_workflows_terminal_success':False,
        'latest_workflow_failures':[],
        'aggregate_run_id':None,
        'prospective_merge_run_id':None,
        'prospective_merge_commit':None,
        'prospective_merge_tree':None,
        'requested_reviewers':[],
        'accepted_non_author_reviews':[],
        'repository_controlled_qualified':False,
    }
    commit_path=work/f'{key}.commit.json'
    if commit_path.exists():
        row['head_tree']=json.loads(commit_path.read_text())['tree']['sha']
    prs_path=work/f'{key}.prs.json'
    if prs_path.exists():
        prs=json.loads(prs_path.read_text())
        if prs:
            prn=prs[0]['number']
            row['pull_request']={
                'number':prn,
                'title':prs[0]['title'],
                'draft':prs[0]['isDraft'],
                'url':prs[0]['url'],
                'base_branch':prs[0]['baseRefName'],
                'base_commit':prs[0]['baseRefOid'],
            }
            pr=json.loads((work/f'{key}.pr.json').read_text())
            row['prospective_merge_commit']=pr.get('merge_commit_sha')
            row['requested_reviewers']=[r['login'] for r in pr.get('requested_reviewers',[])]
            merge=row['prospective_merge_commit']
            if merge:
                # The run script deliberately does not infer tree identity from source tree.
                row['prospective_merge_tree']='read-by-qualifier'
            reviews=json.loads((work/f'{key}.reviews.json').read_text())
            author=pr.get('user',{}).get('login')
            latest_reviews={}
            for review in reviews:
                login=review.get('user',{}).get('login')
                if login:
                    latest_reviews[login]=review
            row['accepted_non_author_reviews']=sorted(
                login for login,review in latest_reviews.items()
                if login != author and review.get('state') == 'APPROVED'
            )
            runs=json.loads((work/f'{key}.runs.json').read_text()).get('workflow_runs',[])
            latest={}
            for run in runs:
                name=run.get('name') or ''
                if name and (name not in latest or run.get('created_at','') > latest[name].get('created_at','')):
                    latest[name]=run
            row['latest_workflow_names']=len(latest)
            row['latest_workflows_terminal_success']=(
                len(latest) >= 56 and
                all(r.get('status') == 'completed' and r.get('conclusion') == 'success' for r in latest.values())
            )
            row['latest_workflow_failures']=[
                {
                    'name':name,
                    'run_id':run.get('id'),
                    'status':run.get('status'),
                    'conclusion':run.get('conclusion'),
                }
                for name,run in sorted(latest.items())
                if run.get('status') != 'completed' or run.get('conclusion') != 'success'
            ]
            row['aggregate_run_id']=latest.get('trillionnium-game-merge-gate',{}).get('id')
            row['prospective_merge_run_id']=latest.get('prospective-merge-gate',{}).get('id')
            row['repository_controlled_qualified']=(
                row['latest_workflows_terminal_success'] and
                row['aggregate_run_id'] is not None and
                row['prospective_merge_run_id'] is not None and
                row['prospective_merge_commit'] is not None
            )
    layers.append(row)

value={
    'schema':'trillionnium.database-closure-live-ledger.v1',
    'repository':'TrillionniumFoundation/TrillionniumGame',
    'generated_at':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    'layers':layers,
    'summary':{
        'expected_layers':len(branches),
        'materialized_layers':sum(row['head_commit'] is not None for row in layers),
        'open_pull_requests':sum(row['pull_request'] is not None for row in layers),
        'repository_controlled_qualified_layers':sum(row['repository_controlled_qualified'] for row in layers),
        'independently_approved_layers':sum(bool(row['accepted_non_author_reviews']) for row in layers),
    },
    'external_blockers':[
        'conflict-free role-qualified database and data-integrity acceptance',
        'administrator-scoped branch/ruleset/actions/environment read-back',
        'PostgreSQL primary failover and approved PITR/RPO/RTO evidence',
        'CockroachDB node and leaseholder failover plus approved recovery objectives',
        '24h 72h and 7d endurance and capacity evidence',
        'fourteen denominator family locks and separate global SG1 decision',
        'complete Nakama API RTAPI Runtime Console provider IAP and official SDK evidence',
        'ordinary protected admission merged-main packet production promotion cutover and retirement decisions',
    ],
    'claim_boundary':{
        'all_gaps_closed':False,
        'accepted_evidence':False,
        'independent_acceptance':False,
        'production_ready':False,
        'cutover_authorized':False,
        'nakama_retired':False,
    },
}
output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n',encoding='utf-8')
print(json.dumps(value['summary'],sort_keys=True))
PY

cd "$ROOT"
git add "$OUTPUT"
git diff --cached --check
if git diff --cached --quiet; then
  exit 0
fi
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
git commit -m 'status: refresh database closure live ledger'
git push origin "HEAD:refs/heads/codex/plan-v32-controller-v2-2026-09-09"
