#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
ROOT=${ROOT:-$GITHUB_WORKSPACE/controller}
BRANCH=${BRANCH:-codex/plan-v32-controller-v2-2026-09-09}
OUTPUT="$ROOT/docs/status/PLAN_V32_CONTROLLER_RUNS_V2.json"
LOG_DIR="$ROOT/docs/internal/plan-v32-controller-failures-v2"
mkdir -p "$(dirname "$OUTPUT")" "$LOG_DIR"
rm -f "$LOG_DIR"/*.log

workflows=(
  plan-v32-controller-v4-20260909.yml
  pg-authority-storage-controller-20260909.yml
  commit-fault-boundary-controller-v4-20260909.yml
  postgresql-semantic-recovery-controller-v2-20260909.yml
  cockroachdb-semantic-recovery-controller-20260909.yml
  postgresql-recovery-barrier-controller-20260909.yml
  postgresql-connection-fault-controller-20260909.yml
  postgresql-primary-failover-controller-20260909.yml
  cockroachdb-node-failover-controller-20260909.yml
  postgresql-pitr-controller-20260909.yml
  remote-mac-provider-controller-20260909.yml
  durability-state-model-controller-20260909.yml
  database-capacity-endurance-controller-20260909.yml
  denominator-review-packet-controller-20260909.yml
  github-governance-readback-controller-20260909.yml
  remote-mac-unix-transport-controller-20260909.yml
  cutover-state-machine-controller-20260909.yml
  stacked-database-pr-qualifier-v15-20260909.yml
)
work="$RUNNER_TEMP/plan-v32-controller-snapshot-v2"
rm -rf "$work"
mkdir -p "$work"

for workflow in "${workflows[@]}"; do
  key=${workflow%.yml}
  if gh api "/repos/${GITHUB_REPOSITORY}/actions/workflows/${workflow}/runs?branch=${BRANCH}&per_page=1" \
      > "$work/${key}.runs.json" 2> "$work/${key}.error"; then
    run_id=$(jq -r '.workflow_runs[0].id // empty' "$work/${key}.runs.json")
    if [[ -n "$run_id" ]]; then
      gh api "/repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/jobs?per_page=100" \
        > "$work/${key}.jobs.json" || :
      conclusion=$(jq -r '.workflow_runs[0].conclusion // empty' "$work/${key}.runs.json")
      if [[ "$conclusion" == failure ]]; then
        gh run view "$run_id" --repo "$GITHUB_REPOSITORY" --log-failed \
          > "$LOG_DIR/${key}.log" 2>&1 || printf 'failed to retrieve failed log for run %s\n' "$run_id" > "$LOG_DIR/${key}.log"
      fi
    fi
  fi
done

python3 - "$work" "$OUTPUT" "$LOG_DIR" "${workflows[@]}" <<'PY'
from __future__ import annotations
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

work=Path(sys.argv[1]); output=Path(sys.argv[2]); log_dir=Path(sys.argv[3]); workflows=sys.argv[4:]
rows=[]
terminal_bad={"failure","cancelled","timed_out","action_required","startup_failure","stale"}
for workflow in workflows:
    key=workflow.removesuffix('.yml')
    runs_path=work/f'{key}.runs.json'
    error_path=work/f'{key}.error'
    row={
        'workflow':workflow,
        'run_id':None,
        'status':'absent',
        'conclusion':None,
        'head_sha':None,
        'created_at':None,
        'updated_at':None,
        'jobs':[],
        'failure_log':None,
        'failure_log_sha256':None,
        'api_error':None,
    }
    if runs_path.exists():
        value=json.loads(runs_path.read_text())
        runs=value.get('workflow_runs',[])
        if runs:
            run=runs[0]
            row.update(
                run_id=run.get('id'),
                status=run.get('status'),
                conclusion=run.get('conclusion'),
                head_sha=run.get('head_sha'),
                created_at=run.get('created_at'),
                updated_at=run.get('updated_at'),
            )
            jobs_path=work/f'{key}.jobs.json'
            if jobs_path.exists():
                jobs=json.loads(jobs_path.read_text()).get('jobs',[])
                row['jobs']=[
                    {
                        'id':job.get('id'),
                        'name':job.get('name'),
                        'status':job.get('status'),
                        'conclusion':job.get('conclusion'),
                    }
                    for job in jobs
                ]
            log=log_dir/f'{key}.log'
            if log.exists():
                row['failure_log']=log.relative_to(output.parents[2]).as_posix()
                row['failure_log_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
        elif error_path.exists() and error_path.read_text().strip():
            row['api_error']=error_path.read_text().strip()
    elif error_path.exists():
        row['api_error']=error_path.read_text().strip()
    rows.append(row)

summary={
    'expected':len(rows),
    'absent':sum(row['status']=='absent' for row in rows),
    'queued_or_running':sum(row['status'] in {'queued','in_progress','waiting','pending','requested'} for row in rows),
    'success':sum(row['status']=='completed' and row['conclusion']=='success' for row in rows),
    'failure':sum(row['status']=='completed' and row['conclusion'] in terminal_bad for row in rows),
    'other_terminal':sum(row['status']=='completed' and row['conclusion'] not in terminal_bad|{'success',None} for row in rows),
}
value={
    'schema':'trillionnium.plan-v32-controller-runs.v2',
    'generated_at':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    'repository':'TrillionniumFoundation/TrillionniumGame',
    'controller_branch':BRANCH if (BRANCH := 'codex/plan-v32-controller-v2-2026-09-09') else None,
    'summary':summary,
    'runs':rows,
    'claim_boundary':{
        'all_controllers_success':summary['success']==len(rows),
        'all_gaps_closed':False,
        'accepted_evidence':False,
        'independent_acceptance':False,
        'production_ready':False,
    },
}
output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(json.dumps(summary,sort_keys=True))
PY

cd "$ROOT"
git add "$OUTPUT" "$LOG_DIR"
git diff --cached --check
if git diff --cached --quiet; then
  exit 0
fi
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
for attempt in $(seq 1 10); do
  git commit -m 'status: snapshot expanded Plan v3.2 controller runs' || :
  if git push origin "HEAD:refs/heads/${BRANCH}"; then
    exit 0
  fi
  git fetch origin "$BRANCH"
  git rebase FETCH_HEAD
  sleep $((attempt * 2))
done
exit 1
