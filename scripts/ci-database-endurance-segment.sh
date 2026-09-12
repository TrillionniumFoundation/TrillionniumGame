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
import hashlib
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
output = Path(sys.argv[2])
capacity = json.loads(source.read_text(encoding="utf-8"))
if capacity.get("schema") != "trillionnium.database-capacity-segment.v1":
    raise SystemExit("unexpected capacity manifest schema")


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


segment = {
    "schema": "trillionnium.database-endurance-segment.v1",
    "segment_index": int(sys.argv[3]),
    "previous_segment_sha256": sys.argv[4],
    "capacity_manifest_sha256": hashlib.sha256(canonical(capacity)).hexdigest(),
    "capacity_manifest": capacity,
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(canonical(segment))
print(hashlib.sha256(output.read_bytes()).hexdigest())
PY
