#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
ROOT=${ROOT:-$GITHUB_WORKSPACE/controller}
BRANCH=${BRANCH:-codex/plan-v32-controller-v2-2026-09-09}
OUTPUT="$ROOT/docs/status/PLAN_V32_CLOSURE_LEDGER_V2.json"
WORK="$RUNNER_TEMP/plan-v32-closure-ledger-v2"
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
)

for branch in "${branches[@]}"; do
  key=${branch//\//__}
  head=$(git -C "$ROOT" ls-remote origin "refs/heads/${branch}" | awk '{print $1}')
  printf '%s\n' "$head" > "$WORK/${key}.head"
  [[ -n "$head" ]] || continue
  gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${head}" > "$WORK/${key}.commit.json"
  gh pr list --repo "$GITHUB_REPOSITORY" --state open --head "$branch" \
    --json number,title,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,url,author \
    > "$WORK/${key}.prs.json"
  pr=$(jq -r '.[0].number // empty' "$WORK/${key}.prs.json")
  [[ -n "$pr" ]] || continue
  gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr}" > "$WORK/${key}.pr.json"
  gh api "/repos/${GITHUB_REPOSITORY}/pulls/${pr}/reviews?per_page=100" \
    > "$WORK/${key}.reviews.json"
  gh api "/repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${head}&event=pull_request&per_page=100" \
    > "$WORK/${key}.runs.json"
done

# Read the latest available gap register from the most downstream materialized branch.
tip_branch=''
for ((index=${#branches[@]}-1; index>=0; index--)); do
  branch=${branches[$index]}
  key=${branch//\//__}
  if [[ -s "$WORK/${key}.head" ]] && [[ -n "$(cat "$WORK/${key}.head")" ]]; then
    tip_branch=$branch
    break
  fi
done
if [[ -n "$tip_branch" ]]; then
  gh api "/repos/${GITHUB_REPOSITORY}/contents/docs/status/GAP_REGISTER.json?ref=${tip_branch}" \
    --jq '.content' | tr -d '\n' | base64 -d > "$WORK/gap-register.json"
  for path in \
    docs/status/github-governance-readback/after-evaluation.json \
    docs/review/denominator-family-packets/index.json \
    docs/status/CUTOVER_STATE.json; do
    key=${path//\//__}
    if encoded=$(gh api "/repos/${GITHUB_REPOSITORY}/contents/${path}?ref=${tip_branch}" --jq '.content' 2>/dev/null); then
      printf '%s' "$encoded" | tr -d '\n' | base64 -d > "$WORK/${key}.json"
    fi
  done
fi

python3 - "$WORK" "$OUTPUT" "$tip_branch" "${branches[@]}" <<'PY'
from __future__ import annotations
import datetime as dt
import json
import sys
from pathlib import Path

work=Path(sys.argv[1]); output=Path(sys.argv[2]); tip_branch=sys.argv[3] or None; branches=sys.argv[4:]
terminal_bad={"failure","cancelled","timed_out","action_required","startup_failure","stale","skipped","neutral"}
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
        'prospective_merge_commit':None,
        'prospective_merge_tree':None,
        'latest_native_workflow_count':0,
        'latest_native_workflows_terminal_success':False,
        'workflow_failures_or_pending':[],
        'aggregate_run_id':None,
        'prospective_merge_run_id':None,
        'requested_reviewers':[],
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
            summary=prs[0]
            prn=summary['number']
            row['pull_request']={'number':prn,'title':summary['title'],'url':summary['url']}
            row['base_branch']=summary['baseRefName']
            row['base_commit']=summary['baseRefOid']
            row['draft']=summary['isDraft']
            pr=json.loads((work/f'{key}.pr.json').read_text())
            row['prospective_merge_commit']=pr.get('merge_commit_sha')
            row['requested_reviewers']=sorted(r['login'] for r in pr.get('requested_reviewers',[]) if r.get('login'))
            author=pr.get('user',{}).get('login')
            reviews=json.loads((work/f'{key}.reviews.json').read_text())
            latest_review={}
            for review in reviews:
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
                if name and (name not in latest or run.get('created_at','') > latest[name].get('created_at','')):
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
            prospective=latest.get('prospective-merge-gate')
            if prospective and prospective.get('status') == 'completed' and prospective.get('conclusion') == 'success':
                row['prospective_merge_tree']='bound-in-retained-prospective-merge-receipt'
            row['repository_controlled_qualified']=(
                row['latest_native_workflows_terminal_success'] and
                row['aggregate_run_id'] is not None and
                row['prospective_merge_run_id'] is not None and
                row['prospective_merge_commit'] is not None and
                row['head_commit'] == summary.get('headRefOid')
            )
            row['independently_accepted']=bool(row['independent_approvals'])
    layers.append(row)

gaps=[]
gap_path=work/'gap-register.json'
if gap_path.exists():
    value=json.loads(gap_path.read_text())
    closed={'closed','rejected','superseded'}
    gaps=[
        {
            'id':row.get('id'),
            'severity':row.get('severity'),
            'status':row.get('status'),
            'owner_role':row.get('owner_role'),
            'external_dependency':row.get('external_dependency'),
        }
        for row in value.get('gaps',[])
        if row.get('severity') in {'P0','P1'} and row.get('status') not in closed
    ]
    gaps.sort(key=lambda row:(row.get('severity') or '',row.get('id') or ''))

def load_optional(name: str):
    path=work/name
    return json.loads(path.read_text()) if path.exists() else None

governance=load_optional('docs__status__github-governance-readback__after-evaluation.json')
denominator=load_optional('docs__review__denominator-family-packets__index.json')
cutover=load_optional('docs__status__CUTOVER_STATE.json')
summary={
    'expected_layers':len(layers),
    'materialized_layers':sum(row['materialized'] for row in layers),
    'open_pull_requests':sum(row['pull_request'] is not None for row in layers),
    'repository_controlled_qualified_layers':sum(row['repository_controlled_qualified'] for row in layers),
    'independently_accepted_layers':sum(row['independently_accepted'] for row in layers),
    'open_p0_p1_count':len(gaps),
    'governance_all_required_pass':bool(governance and governance.get('all_required_pass')),
    'denominator_family_count':denominator.get('family_count') if denominator else None,
    'denominator_leaf_count':denominator.get('leaf_count') if denominator else None,
    'cutover_state':cutover.get('state') if cutover else None,
}
external_blockers=[
    {'id':'EXT-REVIEWER-CAPACITY','actor':'conflict-free role-qualified human reviewers','fact':'current exact-object decisions for governance security database protocol compatibility SRE and production promotion'},
    {'id':'EXT-GITHUB-ADMIN','actor':'repository or organization administrator','fact':'complete no-bypass branch/ruleset/Actions/environment read-back and harmless negative merge rehearsal'},
    {'id':'EXT-DENOMINATOR-ACCEPTANCE','actor':'independent compatibility legal and domain reviewers','fact':'fourteen leaf-complete family decisions and a distinct global SG1 decision'},
    {'id':'EXT-KMS-HSM','actor':'security platform and cryptography reviewers','fact':'approved vendor KMS/HSM adapter with IAM audit rotation revoke latency and failure evidence'},
    {'id':'EXT-ENDURANCE','actor':'SRE performance and database reviewers','fact':'accepted capacity thresholds plus complete 24h 72h and 7d exact-candidate ledgers'},
    {'id':'EXT-RPO-RTO','actor':'service owner SRE and database reviewers','fact':'approved RPO/RTO bound to accepted failover PITR and rollback packets'},
    {'id':'EXT-COMPATIBILITY-IMPLEMENTATION','actor':'product runtime console provider and SDK owners','fact':'complete Nakama API RTAPI Runtime Console provider IAP and official SDK implementation/evidence'},
    {'id':'EXT-PROMOTION','actor':'independent release authority','fact':'ordinary protected admission merged-main evidence shadow exclusive canary production and separate retirement decisions'},
]
all_repository_layers=(summary['materialized_layers']==len(layers) and summary['repository_controlled_qualified_layers']==len(layers))
all_external=False
value={
    'schema':'trillionnium.plan-v32-closure-ledger.v2',
    'repository':'TrillionniumFoundation/TrillionniumGame',
    'generated_at':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    'tip_branch_examined':tip_branch,
    'summary':summary,
    'layers':layers,
    'open_p0_p1':gaps,
    'governance_evaluation':governance,
    'denominator_review_index_summary':None if denominator is None else {
        'family_count':denominator.get('family_count'),
        'leaf_count':denominator.get('leaf_count'),
        'claim_boundary':denominator.get('claim_boundary'),
    },
    'cutover_state':cutover,
    'external_blockers':external_blockers,
    'claim_boundary':{
        'all_repository_controlled_layers_qualified':all_repository_layers,
        'all_external_facts_accepted':all_external,
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
if git diff --cached --quiet; then
  exit 0
fi
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
for attempt in $(seq 1 12); do
  git commit -m 'status: refresh Plan v3.2 closure ledger v2' || :
  if git push origin "HEAD:refs/heads/${BRANCH}"; then
    exit 0
  fi
  git fetch origin "$BRANCH"
  git rebase FETCH_HEAD
  sleep $((attempt * 2))
done
exit 1
