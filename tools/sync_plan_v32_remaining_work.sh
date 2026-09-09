#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?}"
: "${GITHUB_REPOSITORY:?}"
ROOT=${ROOT:-$GITHUB_WORKSPACE/controller}
SOURCE_BRANCH=${SOURCE_BRANCH:-codex/plan-v32-external-blocker-handoff-2026-09-09}
CONTROL_BRANCH=${CONTROL_BRANCH:-codex/plan-v32-controller-v2-2026-09-09}
WORK="$RUNNER_TEMP/plan-v32-remaining-work"
OUTPUT="$ROOT/docs/status/PLAN_V32_REMAINING_WORK.json"
rm -rf "$WORK"
mkdir -p "$WORK"

source_head=''
for _ in $(seq 1 120); do
  source_head=$(git -C "$ROOT" ls-remote origin "refs/heads/${SOURCE_BRANCH}" | awk '{print $1}')
  [[ -n "$source_head" ]] && break
  sleep 10
done
test -n "$source_head"
source_tree=$(gh api "/repos/${GITHUB_REPOSITORY}/git/commits/${source_head}" --jq '.tree.sha')
fetch_content() {
  local path=$1 output=$2 encoded
  encoded=$(gh api "/repos/${GITHUB_REPOSITORY}/contents/${path}?ref=${SOURCE_BRANCH}" --jq '.content')
  printf '%s' "$encoded" | tr -d '\n' | base64 -d > "$output"
}
fetch_content docs/status/GAP_REGISTER.json "$WORK/gaps.json"
fetch_content docs/review/denominator-family-packets/index.json "$WORK/denominator-index.json"

python3 - "$WORK/gaps.json" "$WORK/denominator-index.json" "$OUTPUT" "$source_head" "$source_tree" <<'PY'
from __future__ import annotations
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

gaps_path=Path(sys.argv[1]); denominator_path=Path(sys.argv[2]); output=Path(sys.argv[3]); source_head=sys.argv[4]; source_tree=sys.argv[5]
gaps=json.loads(gaps_path.read_text()); denominator=json.loads(denominator_path.read_text())
closed={'closed','rejected','superseded'}
external_words=('independent','administrator','accepted','approval','reviewer','production','public online','cutover','retirement','rpo','rto','kms','hsm','legal')
rows=[]
for gap in gaps.get('gaps',[]):
    if gap.get('severity') not in {'P0','P1'} or gap.get('status') in closed:
        continue
    criteria=[]
    for index,text in enumerate(gap.get('close_criteria') or []):
        task_id=hashlib.sha256(f"{gap.get('id')}\0{index}\0{text}".encode()).hexdigest()[:24]
        lowered=str(text).lower()
        kind='external-fact' if gap.get('external_dependency') or any(word in lowered for word in external_words) else 'repository-work'
        criteria.append({'task_id':task_id,'criterion_index':index,'text':text,'execution_class':kind,'complete':False})
    rows.append({
        'gap_id':gap.get('id'),
        'severity':gap.get('severity'),
        'category':gap.get('category'),
        'title':gap.get('title'),
        'owner_role':gap.get('owner_role'),
        'status':gap.get('status'),
        'external_dependency':gap.get('external_dependency'),
        'affected_paths':gap.get('affected_paths') or [],
        'required_evidence_types':gap.get('required_evidence_types') or [],
        'issue_refs':gap.get('issue_refs') or [],
        'criteria':criteria,
    })
rows.sort(key=lambda row:(row['severity'],row['gap_id']))
families=[]
for family in denominator.get('families',[]):
    families.append({
        'family_id':family.get('family_id'),
        'packet':family.get('packet'),
        'packet_sha256':family.get('packet_sha256'),
        'source_manifest':family.get('source_manifest'),
        'source_manifest_sha256':family.get('source_manifest_sha256'),
        'leaf_count':family.get('leaf_count'),
        'classification_complete':False,
        'implementation_complete':False,
        'oracle_differential_complete':False,
        'official_sdk_complete':False,
        'independent_family_decision':False,
    })
value={
    'schema':'trillionnium.plan-v32-remaining-work.v1',
    'generated_at':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    'source_branch':'codex/plan-v32-external-blocker-handoff-2026-09-09',
    'source_head':source_head,
    'source_tree':source_tree,
    'open_gap_count':len(rows),
    'close_criterion_count':sum(len(row['criteria']) for row in rows),
    'repository_work_count':sum(task['execution_class']=='repository-work' for row in rows for task in row['criteria']),
    'external_fact_count':sum(task['execution_class']=='external-fact' for row in rows for task in row['criteria']),
    'denominator_family_count':len(families),
    'denominator_leaf_count':sum(int(row.get('leaf_count') or 0) for row in families),
    'gaps':rows,
    'denominator_families':families,
    'claim_boundary':{
        'all_repository_work_complete':False,
        'all_external_facts_accepted':False,
        'all_families_accepted':False,
        'global_sg1_accepted':False,
        'all_gaps_closed':False,
        'production_ready':False,
    },
}
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(json.dumps({key:value[key] for key in ('open_gap_count','close_criterion_count','repository_work_count','external_fact_count','denominator_family_count','denominator_leaf_count')},sort_keys=True))
PY

# Route each open gap to its existing issue, or create one when the register has no route.
while IFS= read -r gap; do
  gap_id=$(jq -r '.gap_id' <<<"$gap")
  title=$(jq -r '.title' <<<"$gap")
  severity=$(jq -r '.severity' <<<"$gap")
  owner=$(jq -r '.owner_role' <<<"$gap")
  status=$(jq -r '.status' <<<"$gap")
  refs=$(jq -r '.issue_refs[]? | sub("^#";"")' <<<"$gap")
  issue=$(head -n1 <<<"$refs")
  if [[ -z "$issue" ]]; then
    issue=$(gh issue list --repo "$GITHUB_REPOSITORY" --state all --search "${gap_id} in:title" --json number,title --jq 'map(select(.title | contains("'"$gap_id"'")))[0].number // empty')
  fi
  if [[ -z "$issue" ]]; then
    body=$(cat <<EOF
<!-- plan-v32-gap:${gap_id} -->
## ${gap_id}: ${title}

Exact source work queue: \`${source_head}\` / tree \`${source_tree}\`.

Owner role: \`${owner}\`  
Severity: \`${severity}\`  
Register status: \`${status}\`

This issue is generated because the canonical gap register had no existing issue route. Every close criterion and evidence type is retained in \`docs/status/PLAN_V32_REMAINING_WORK.json\`. Source, CI, evidence admission, independent acceptance and production promotion remain distinct.
EOF
)
    issue=$(gh issue create --repo "$GITHUB_REPOSITORY" --title "[Plan v3.2 ${severity}] ${gap_id}: ${title}" --body "$body")
    issue=${issue##*/}
  fi
  marker="<!-- plan-v32-workqueue:${gap_id}:${source_head} -->"
  exists=$(gh api --paginate "/repos/${GITHUB_REPOSITORY}/issues/${issue}/comments?per_page=100" --jq '.[].body' | grep -F "$marker" || :)
  if [[ -z "$exists" ]]; then
    criteria=$(jq -r '.criteria[] | "- [ ] `" + .task_id + "` `" + .execution_class + "` — " + .text' <<<"$gap")
    evidence=$(jq -r '.required_evidence_types | if length==0 then "- none declared" else .[] | "- `" + . + "`" end' <<<"$gap")
    comment=$(cat <<EOF
${marker}
## Current exact Plan v3.2 work queue

\`\`\`text
source_head=${source_head}
source_tree=${source_tree}
gap=${gap_id}
severity=${severity}
owner_role=${owner}
register_status=${status}
\`\`\`

### Close criteria
${criteria}

### Required evidence types
${evidence}

No item is complete merely because this queue exists. Exact-head execution, admitted retained evidence and role-qualified independent acceptance remain required.
EOF
)
    gh api --method POST "/repos/${GITHUB_REPOSITORY}/issues/${issue}/comments" -f body="$comment" >/dev/null
  fi
done < <(jq -c '.gaps[]' "$OUTPUT")

# One implementation/review issue per immutable denominator family.
while IFS= read -r family; do
  family_id=$(jq -r '.family_id' <<<"$family")
  leaf_count=$(jq -r '.leaf_count' <<<"$family")
  packet=$(jq -r '.packet' <<<"$family")
  packet_sha=$(jq -r '.packet_sha256' <<<"$family")
  title="[Plan v3.2 Denominator] ${family_id}: classify and implement ${leaf_count} leaves"
  issue=$(gh issue list --repo "$GITHUB_REPOSITORY" --state all --search "${family_id} in:title" --json number,title --jq 'map(select(.title == "'"$title"'"))[0].number // empty')
  if [[ -z "$issue" ]]; then
    body=$(cat <<EOF
<!-- plan-v32-denominator:${family_id} -->
## Immutable family identity

\`\`\`text
source_head=${source_head}
source_tree=${source_tree}
family_id=${family_id}
leaf_count=${leaf_count}
packet=docs/review/denominator-family-packets/${packet}
packet_sha256=${packet_sha}
\`\`\`

- [ ] classify every leaf with rationale and evidence mapping
- [ ] implement every required/unimplemented leaf without denominator reduction
- [ ] execute immutable-oracle differential ten times per approved lane
- [ ] execute supported official SDK consumers
- [ ] obtain a conflict-free family decision bound to the exact candidate

A candidate packet is not classification, implementation or acceptance. The issue must remain open while any leaf is unclassified, blocked, unimplemented or unaccepted.
EOF
)
    gh issue create --repo "$GITHUB_REPOSITORY" --title "$title" --body "$body" >/dev/null
  fi
done < <(jq -c '.denominator_families[]' "$OUTPUT")

cd "$ROOT"
git add "$OUTPUT"
git diff --cached --check
if git diff --cached --quiet; then exit 0; fi
git config user.name github-actions[bot]
git config user.email 41898282+github-actions[bot]@users.noreply.github.com
for attempt in $(seq 1 12); do
  git commit -m 'status: sync zero-omission Plan v3.2 remaining work' || :
  if git push origin "HEAD:refs/heads/${CONTROL_BRANCH}"; then exit 0; fi
  git fetch origin "$CONTROL_BRANCH"
  git rebase FETCH_HEAD
  sleep $((attempt * 2))
done
exit 1
