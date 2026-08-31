#!/usr/bin/env python3
"""Materialize the reviewed PR57 retained-log evidence repair and self-delete."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / ".trnm-final-payload"
WORKFLOW = ROOT / ".github/workflows/temporary-finalize-pr57-log-evidence.yml"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact anchor, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex anchor, found {count}")
    return result


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


payload_map = {
    "emit-actions-log-artifact.py": "scripts/emit-actions-log-artifact.py",
    "verify-actions-log-artifact.py": "scripts/verify-actions-log-artifact.py",
    "test_actions_log_artifact.py": "tests/control_plane/test_actions_log_artifact.py",
    "test_actions_log_verifier.py": "tests/control_plane/test_actions_log_verifier.py",
    "outbox-final-attempt-reaper.yml": ".github/workflows/outbox-final-attempt-reaper.yml",
}
for source_name, destination in payload_map.items():
    source = PAYLOAD / source_name
    if not source.is_file():
        raise SystemExit(f"missing final payload: {source}")
    target = ROOT / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)

# Bind the outbox evidence archive to the exact workflow/run/job/tree/migration
# identity and remove the background tee that could mutate an archived log after
# its checksum manifest was sealed.
outbox_path = "scripts/ci-outbox-final-attempt-reaper.sh"
outbox = read(outbox_path)
outbox = replace_once(
    outbox,
    'cd "$root"\n\nfor command in docker python3 cargo git sha256sum; do\n',
    'cd "$root"\nrepository=${CANDIDATE_REPOSITORY:-TrillionniumFoundation/TrillionniumGame}\n\nfor command in docker python3 cargo git sha256sum; do\n',
    "outbox repository identity",
)
outbox = replace_once(
    outbox,
    'run_id=${TRNM_RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)-$$}\nevidence_root=${TRNM_EVIDENCE_ROOT:-run/outbox-final-attempt-reaper}\nevidence="$evidence_root/$profile/$run_id"\n',
    'evidence_run_id=${TRNM_RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)-$$}\nworkflow_run_id=${GITHUB_RUN_ID:-local}\nrun_attempt=${GITHUB_RUN_ATTEMPT:-1}\nworkflow_name=outbox-final-attempt-reaper\nworkflow_path=.github/workflows/outbox-final-attempt-reaper.yml\njob_key=${GITHUB_JOB:-live-profile}\njob_name="live-profile ($profile)"\nevidence_root=${TRNM_EVIDENCE_ROOT:-run/outbox-final-attempt-reaper}\nevidence="$evidence_root/$profile/$evidence_run_id"\n',
    "outbox run identity",
)
outbox = outbox.replace("${run_id//", "${evidence_run_id//")
outbox = replace_once(
    outbox,
    'exec > >(tee "$evidence/logs/run.log") 2>&1\n\n',
    "",
    "remove mutable outbox run tee",
)
outbox = regex_once(
    outbox,
    r'''printf '%s\\n' \\\n  "repository=TrillionniumFoundation/TrillionniumGame" \\\n  "commit=\$commit" \\\n  "tree=\$tree" \\\n  "profile=\$profile" \\\n  "image=\$image" \\\n  "migration=\$migration" \\\n  "migration_blob_sha1=\$migration_blob_sha1" \\\n  "run_id=\$run_id" \\\n  >"\$evidence/identity\.env"\n''',
    '''printf '%s\\n' \\
  "repository=$repository" \\
  "commit=$commit" \\
  "tree=$tree" \\
  "profile=$profile" \\
  "image=$image" \\
  "run_id=$workflow_run_id" \\
  "run_attempt=$run_attempt" \\
  "evidence_run_id=$evidence_run_id" \\
  "workflow=$workflow_name" \\
  "workflow_path=$workflow_path" \\
  "job_key=$job_key" \\
  "job_name=$job_name" \\
  "migration=$migration" \\
  "migration_blob_sha1=$migration_blob_sha1" \\
  >"$evidence/identity.env"\n''',
    "outbox identity.env",
)
old_outbox_ready = '''  ready=false
  for _ in $(seq 1 120); do
    container_running || break
    ready_count=$(docker logs "$container" 2>&1 | grep -c 'database system is ready to accept connections' || true)
    if (( ready_count >= 2 )) && docker exec -e PGPASSWORD="$password" "$container" \
      psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm -c 'SELECT 1' 2>/dev/null | grep -qx '1'; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "$ready" == true ]]
'''
new_outbox_ready = '''  ready=false
  stable_sql_successes=0
  for _ in $(seq 1 240); do
    container_running || break
    if docker logs "$container" 2>&1 | \
         grep -q 'PostgreSQL init process complete; ready for start up.' && \
       docker exec -e PGPASSWORD="$password" "$container" \
         psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm \
         -c 'SELECT 1' 2>/dev/null | grep -qx '1'; then
      stable_sql_successes=$((stable_sql_successes + 1))
    else
      stable_sql_successes=0
    fi
    if (( stable_sql_successes >= 5 )); then
      ready=true
      break
    fi
    sleep 0.5
  done
  [[ "$ready" == true ]]
  for _ in $(seq 1 3); do
    container_running
    docker exec -e PGPASSWORD="$password" "$container" \
      psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm \
      -c 'SELECT 1' 2>/dev/null | grep -qx '1'
  done
  printf 'final_entrypoint_marker=true\\nstable_sql_successes=%s\\npost_stability_transactions=3\\n' \
    "$stable_sql_successes" >"$evidence/logs/postgresql-final-readiness.env"
'''
outbox = replace_once(
    outbox, old_outbox_ready, new_outbox_ready, "outbox PostgreSQL final readiness"
)
write(outbox_path, outbox)

# Prevent the server live gate from accepting the transient init server.
server_path = "scripts/ci-trnm-server-live.sh"
server = read(server_path)
old_server_ready = '''  if [[ "$profile" == postgresql ]]; then
    ready=false
    for _ in $(seq 1 120); do
      if container_running && docker exec -e PGPASSWORD="$password" "$container" \
        pg_isready -U postgres -d trnm >/dev/null 2>&1; then
        ready=true
        break
      fi
      sleep 1
    done
    [[ "$ready" == true ]]
'''
new_server_ready = '''  if [[ "$profile" == postgresql ]]; then
    ready=false
    stable_sql_successes=0
    for _ in $(seq 1 240); do
      container_running || break
      if docker logs "$container" 2>&1 | \
           grep -q 'PostgreSQL init process complete; ready for start up.' && \
         docker exec -e PGPASSWORD="$password" "$container" \
           psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm \
           -c 'SELECT 1' 2>/dev/null | grep -qx '1'; then
        stable_sql_successes=$((stable_sql_successes + 1))
      else
        stable_sql_successes=0
      fi
      if (( stable_sql_successes >= 5 )); then
        ready=true
        break
      fi
      sleep 0.5
    done
    [[ "$ready" == true ]]
    for _ in $(seq 1 3); do
      container_running
      docker exec -e PGPASSWORD="$password" "$container" \
        psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm \
        -c 'SELECT 1' 2>/dev/null | grep -qx '1'
    done
    printf 'final_entrypoint_marker=true\\nstable_sql_successes=%s\\npost_stability_transactions=3\\n' \
      "$stable_sql_successes" >"$evidence/postgresql-final-readiness.env"
'''
server = replace_once(
    server, old_server_ready, new_server_ready, "server PostgreSQL final readiness"
)
write(server_path, server)

# Make the server source gate prove the stronger readiness contract exists.
server_workflow_path = ".github/workflows/trnm-server-live.yml"
server_workflow = read(server_workflow_path)
server_workflow = replace_once(
    server_workflow,
    "          bash -n scripts/ci-trnm-server-live.sh\n",
    "          bash -n scripts/ci-trnm-server-live.sh\n"
    "          grep -q 'PostgreSQL init process complete; ready for start up.' scripts/ci-trnm-server-live.sh\n"
    "          grep -q 'stable_sql_successes' scripts/ci-trnm-server-live.sh\n"
    "          grep -q 'post_stability_transactions=3' scripts/ci-trnm-server-live.sh\n",
    "server live source assertions",
)
write(server_workflow_path, server_workflow)

# Add a one-way aggregate dependency that waits for a terminal successful
# producer workflow and independently reconstructs its two completed job logs.
merge_path = ".github/workflows/trillionnium-game-merge-gate.yml"
merge = read(merge_path)
merge = replace_once(
    merge,
    "permissions:\n  contents: read\n",
    "permissions:\n  actions: read\n  contents: read\n  pull-requests: read\n",
    "merge gate read permissions",
)
outbox_job = r'''  outbox-retained-evidence:
    name: outbox-retained-evidence
    runs-on: ubuntu-24.04
    timeout-minutes: 25
    steps:
      - name: Fetch exact candidate without external actions
        shell: bash
        run: |
          set -euo pipefail
          rm -rf "$GITHUB_WORKSPACE"
          git init "$GITHUB_WORKSPACE"
          git -C "$GITHUB_WORKSPACE" remote add origin \
            "https://github.com/${CANDIDATE_REPOSITORY}.git"
          git -C "$GITHUB_WORKSPACE" fetch --no-tags --depth=1 origin "$CANDIDATE_SHA"
          git -C "$GITHUB_WORKSPACE" checkout --detach FETCH_HEAD
          test "$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)" = "$CANDIDATE_SHA"

      - name: Determine whether the exact candidate touches outbox evidence scope
        shell: bash
        env:
          GITHUB_TOKEN: ${{ github.token }}
          EVENT_NAME: ${{ github.event_name }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json
          import os
          import urllib.request

          relevant = False
          if os.environ['EVENT_NAME'] == 'pull_request':
              repository = os.environ['GITHUB_REPOSITORY']
              number = os.environ['PR_NUMBER']
              token = os.environ['GITHUB_TOKEN']
              prefixes = (
                  'crates/trnm-persistence-pg/',
                  'migrations/',
                  'config/database-test-images.json',
                  'scripts/ci-outbox-final-attempt-reaper.sh',
                  'scripts/emit-actions-log-artifact.py',
                  'scripts/verify-actions-log-artifact.py',
                  'tests/control_plane/test_actions_log_',
                  'docs/development/OUTBOX_',
                  '.github/workflows/outbox-final-attempt-reaper.yml',
                  '.github/workflows/trillionnium-game-merge-gate.yml',
              )
              page = 1
              while True:
                  request = urllib.request.Request(
                      f'https://api.github.com/repos/{repository}/pulls/{number}/files?per_page=100&page={page}',
                      headers={
                          'Accept': 'application/vnd.github+json',
                          'Authorization': f'Bearer {token}',
                          'X-GitHub-Api-Version': '2022-11-28',
                          'User-Agent': 'trillionnium-merge-gate/2',
                      },
                  )
                  with urllib.request.urlopen(request, timeout=30) as response:
                      rows = json.load(response)
                  if not isinstance(rows, list):
                      raise SystemExit('pull files response is not a list')
                  for row in rows:
                      filename = row.get('filename') if isinstance(row, dict) else None
                      if isinstance(filename, str) and filename.startswith(prefixes):
                          relevant = True
                  if len(rows) < 100:
                      break
                  page += 1
                  if page > 20:
                      raise SystemExit('pull file pagination exceeded hard bound')
          with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as output:
              output.write(f'OUTBOX_EVIDENCE_RELEVANT={str(relevant).lower()}\n')
          print(json.dumps({'outbox_evidence_relevant': relevant}, sort_keys=True))
          PY

      - name: Verify exact completed outbox job logs
        if: env.OUTBOX_EVIDENCE_RELEVANT == 'true'
        shell: bash
        env:
          GITHUB_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          python3 scripts/verify-actions-log-artifact.py \
            --repository "$CANDIDATE_REPOSITORY" \
            --head-sha "$CANDIDATE_SHA" \
            --discover-completed-run \
            --wait-seconds 1200 \
            --output-directory run/merge-gate-outbox-log-verification

      - name: Record non-applicable exact scope
        if: env.OUTBOX_EVIDENCE_RELEVANT != 'true'
        shell: bash
        run: |
          set -euo pipefail
          printf '%s\n' 'outbox retained evidence: not applicable to this candidate'

'''
merge = replace_once(
    merge,
    "  trillionnium-game-merge-gate:\n",
    outbox_job + "  trillionnium-game-merge-gate:\n",
    "merge gate outbox retained evidence job",
)
merge = replace_once(
    merge,
    "      - source-candidate-boundaries\n    runs-on: ubuntu-24.04\n",
    "      - source-candidate-boundaries\n      - outbox-retained-evidence\n    runs-on: ubuntu-24.04\n",
    "merge gate outbox need",
)
merge = replace_once(merge, "          EXPECTED=13\n", "          EXPECTED=14\n", "merge gate result count")
merge = replace_once(
    merge,
    '              "source-candidate-boundaries": os.environ["SOURCE_BOUNDARY_RESULT"],\n',
    '              "source-candidate-boundaries": os.environ["SOURCE_BOUNDARY_RESULT"],\n'
    '              "outbox-retained-evidence": os.environ["OUTBOX_RETAINED_RESULT"],\n',
    "merge gate result map",
)
merge = replace_once(
    merge,
    "          SOURCE_BOUNDARY_RESULT: ${{ needs.source-candidate-boundaries.result }}\n",
    "          SOURCE_BOUNDARY_RESULT: ${{ needs.source-candidate-boundaries.result }}\n"
    "          OUTBOX_RETAINED_RESULT: ${{ needs.outbox-retained-evidence.result }}\n",
    "merge gate result environment",
)
write(merge_path, merge)

# Record the exact new boundary without altering product claims.
doc_path = "docs/development/OUTBOX_FINAL_ATTEMPT_REAPER.md"
doc = read(doc_path)
heading = "## Exact terminal completed-job-log verifier v2"
if heading not in doc:
    doc += f'''\n{heading}\n\nThe retained-log proof is now a two-stage, fail-closed chain.  The producer\nworkflow reconstructs both completed matrix-job logs while its final verifier\njob is still running.  The aggregate merge gate then waits for a separate,\nterminal-success producer run on the same immutable head and repeats the\nreconstruction independently.\n\nThe verifier binds the positive numeric workflow ID, workflow name and path,\nrepository, event, run ID, run attempt, exact head and tree, a closed-world job\nset, positive runner identities, non-empty successful step sets, digest-pinned\ndatabase images, and each migration's exact Git blob from\n`migrations/MIGRATION_CHAIN.lock.json`.  Real GitHub timestamp-prefixed logs\nare parsed canonically; mixed prefixes, injected markers, interruptions,\ntruncation, duplicate jobs and partially successful runs fail closed.\n\nThe archive checksum manifest excludes itself, uses relative `./...` paths and\nis written only after every included file is stable.  These controls establish\ndiagnostic source/live evidence only; compatibility and production credit\nremain false pending independent acceptance.\n'''
write(doc_path, doc)

# The final product tree must contain no staging, source publisher or probe.
for path in (
    ROOT / "tmp-schema-create-file-probe.txt",
    ROOT / "tmp-schema-probe.txt",
):
    path.unlink(missing_ok=True)
shutil.rmtree(PAYLOAD)
WORKFLOW.unlink(missing_ok=True)
SELF.unlink(missing_ok=True)
print("PR57 retained-log evidence repair materialized; temporary publisher removed")
