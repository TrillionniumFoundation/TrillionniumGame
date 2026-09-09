#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
BRANCH=${BRANCH:-codex/plan-v32-controller-v2-2026-09-09}
OUTPUT=${OUTPUT:-$RUNNER_TEMP/plan-v32-rerun-report.json}
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
report="$RUNNER_TEMP/plan-v32-rerun-lines.jsonl"
: > "$report"
for workflow in "${workflows[@]}"; do
  run=$(gh api "/repos/${GITHUB_REPOSITORY}/actions/workflows/${workflow}/runs?branch=${BRANCH}&per_page=1")
  run_id=$(jq -r '.workflow_runs[0].id // empty' <<<"$run")
  status=$(jq -r '.workflow_runs[0].status // "absent"' <<<"$run")
  conclusion=$(jq -r '.workflow_runs[0].conclusion // ""' <<<"$run")
  attempt=$(jq -r '.workflow_runs[0].run_attempt // 0' <<<"$run")
  action=none
  reason='not eligible'
  if [[ -n "$run_id" && "$status" == completed && "$attempt" -lt 3 ]]; then
    case "$conclusion" in
      failure|cancelled|timed_out|startup_failure)
        gh api --method POST "/repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/rerun-failed-jobs" >/dev/null
        action=rerun-failed-jobs
        reason='terminal failure eligible for one bounded retry'
        ;;
      *) reason='latest run is not a rerunnable failure' ;;
    esac
  elif [[ "$status" != completed ]]; then
    reason='latest run is absent queued or running'
  elif [[ "$attempt" -ge 3 ]]; then
    reason='bounded retry ceiling reached'
  fi
  jq -cn \
    --arg workflow "$workflow" \
    --argjson run_id "${run_id:-null}" \
    --arg status "$status" \
    --arg conclusion "$conclusion" \
    --argjson attempt "$attempt" \
    --arg action "$action" \
    --arg reason "$reason" \
    '{workflow:$workflow,run_id:$run_id,status:$status,conclusion:($conclusion|select(length>0)),run_attempt:$attempt,action:$action,reason:$reason}' \
    >> "$report"
done
jq -s '{schema:"trillionnium.plan-v32-controller-rerun-report.v1",rows:.,summary:{examined:length,rerun:(map(select(.action=="rerun-failed-jobs"))|length),untouched:(map(select(.action=="none"))|length)},claim_boundary:{rerun_is_success:false,all_gaps_closed:false,accepted_evidence:false,production_ready:false}}' "$report" > "$OUTPUT"
cat "$OUTPUT"
