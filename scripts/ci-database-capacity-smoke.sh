#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${TRNM_REQUIRE_LIVE_DATABASE:?TRNM_REQUIRE_LIVE_DATABASE must be explicit}"
test "$TRNM_REQUIRE_LIVE_DATABASE" = 1
: "${TRNM_DATABASE_PROFILE:?TRNM_DATABASE_PROFILE is required}"
: "${TRNM_DATABASE_URL:?TRNM_DATABASE_URL is required}"
: "${PGBENCH_IMAGE:?PGBENCH_IMAGE is required}"
DURATION_SECONDS=${DURATION_SECONDS:-20}
CLIENTS=${CLIENTS:-4}
THREADS=${THREADS:-2}
EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/database-capacity-${TRNM_DATABASE_PROFILE}}
CANDIDATE_COMMIT=${CANDIDATE_COMMIT:-unknown}
CANDIDATE_TREE=${CANDIDATE_TREE:-unknown}
mkdir -p "$EVIDENCE_DIR"

case "$TRNM_DATABASE_PROFILE" in
  postgresql|cockroachdb) ;;
  *) printf 'unsupported profile=%s\n' "$TRNM_DATABASE_PROFILE" >&2; exit 2 ;;
esac
[[ "$DURATION_SECONDS" =~ ^[0-9]+$ ]] && test "$DURATION_SECONDS" -ge 5 && test "$DURATION_SECONDS" -le 21600
[[ "$CLIENTS" =~ ^[0-9]+$ ]] && test "$CLIENTS" -ge 1 && test "$CLIENTS" -le 256
[[ "$THREADS" =~ ^[0-9]+$ ]] && test "$THREADS" -ge 1 && test "$THREADS" -le "$CLIENTS"

started_epoch=$(date +%s)
docker run --rm --network host \
  -v "$ROOT/scripts/database-capacity-workload.sql:/workload.sql:ro" \
  "$PGBENCH_IMAGE" pgbench "$TRNM_DATABASE_URL" -n \
  --client="$CLIENTS" --jobs="$THREADS" --time="$DURATION_SECONDS" \
  --progress=5 --file=/workload.sql \
  > "$EVIDENCE_DIR/pgbench.stdout" \
  2> "$EVIDENCE_DIR/pgbench.stderr"
finished_epoch=$(date +%s)
observed_seconds=$((finished_epoch-started_epoch))
test "$observed_seconds" -ge "$DURATION_SECONDS"

python3 - "$ROOT" "$EVIDENCE_DIR" "$TRNM_DATABASE_PROFILE" \
  "$TRNM_DATABASE_URL" "$PGBENCH_IMAGE" "$DURATION_SECONDS" "$observed_seconds" \
  "$CLIENTS" "$THREADS" "$CANDIDATE_COMMIT" "$CANDIDATE_TREE" <<'PY'
import hashlib,json,re,sys
from pathlib import Path
root=Path(sys.argv[1]); evidence=Path(sys.argv[2])
profile,url,image=sys.argv[3:6]
requested,observed,clients,threads=map(int,sys.argv[6:10])
candidate_commit,candidate_tree=sys.argv[10:12]
stdout=(evidence/'pgbench.stdout').read_text(encoding='utf-8',errors='replace')
stderr=(evidence/'pgbench.stderr').read_text(encoding='utf-8',errors='replace')
combined=stdout+'\n'+stderr
def one(pattern: str, cast, label: str):
    matches=re.findall(pattern,combined,flags=re.I|re.M)
    if not matches: raise SystemExit(f'missing pgbench metric: {label}')
    return cast(matches[-1])
transactions=one(r'number of transactions actually processed:\s*([0-9]+)',int,'transactions')
failed_matches=re.findall(r'number of failed transactions:\s*([0-9]+)',combined,flags=re.I)
failed=int(failed_matches[-1]) if failed_matches else 0
latency=one(r'latency average\s*=\s*([0-9.]+)\s*ms',float,'latency')
tps=one(r'tps\s*=\s*([0-9.]+)',float,'tps')
if transactions <= 0 or failed != 0 or latency <= 0 or tps <= 0:
    raise SystemExit('capacity smoke produced invalid or failed metrics')
def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
workload=root/'scripts/database-capacity-workload.sql'
manifest={
  'schema':'trillionnium.database-capacity-segment.v1',
  'profile':profile,
  'database_endpoint_redacted':'<redacted>',
  'client_image_reference':image,
  'candidate_commit':candidate_commit,
  'candidate_tree':candidate_tree,
  'workload_sha256':digest(workload),
  'requested_duration_seconds':requested,
  'observed_duration_seconds':observed,
  'clients':clients,
  'threads':threads,
  'transactions':transactions,
  'failed_transactions':failed,
  'latency_average_ms':latency,
  'transactions_per_second':tps,
  'stdout_sha256':digest(evidence/'pgbench.stdout'),
  'stderr_sha256':digest(evidence/'pgbench.stderr'),
  'claim_boundary':{
    'capacity_target_accepted':False,
    'performance_accepted':False,
    'endurance_complete':False,
    'independently_accepted':False,
    'production_ready':False,
  },
}
(evidence/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print(json.dumps(manifest,sort_keys=True))
PY
