#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${SEGMENT_INDEX:?SEGMENT_INDEX is required}"
: "${PREVIOUS_SEGMENT_SHA256:?PREVIOUS_SEGMENT_SHA256 is required; use GENESIS for index zero}"
: "${SEGMENT_OUTPUT:?SEGMENT_OUTPUT is required}"
[[ "$SEGMENT_INDEX" =~ ^[0-9]+$ ]]
if [[ "$SEGMENT_INDEX" = 0 ]]; then
  test "$PREVIOUS_SEGMENT_SHA256" = GENESIS
else
  [[ "$PREVIOUS_SEGMENT_SHA256" =~ ^[0-9a-f]{64}$ ]]
fi
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT
export EVIDENCE_DIR="$temp/capacity"
bash "$ROOT/scripts/ci-database-capacity-smoke.sh"
python3 - "$EVIDENCE_DIR/manifest.json" "$SEGMENT_OUTPUT" \
  "$SEGMENT_INDEX" "$PREVIOUS_SEGMENT_SHA256" <<'PY'
import hashlib,json,sys
from pathlib import Path
source=Path(sys.argv[1]); output=Path(sys.argv[2])
value=json.loads(source.read_text())
value['schema']='trillionnium.database-endurance-segment.v1'
value['segment_index']=int(sys.argv[3])
value['previous_segment_sha256']=sys.argv[4]
value['segment_payload_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
print(hashlib.sha256(output.read_bytes()).hexdigest())
PY
