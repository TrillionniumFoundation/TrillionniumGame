#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKER="$ROOT_DIR/scripts/check-world-transition-v1-boundary.sh"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEMP_ROOT"' EXIT

make_case() {
  local name="$1"
  local destination="$TEMP_ROOT/$name"
  mkdir -p "$destination"
  cp -a \
    "$ROOT_DIR/contracts" \
    "$ROOT_DIR/testdata" \
    "$ROOT_DIR/runtime" \
    "$ROOT_DIR/tools" \
    "$ROOT_DIR/tests" \
    "$ROOT_DIR/docs" \
    "$ROOT_DIR/scripts" \
    "$destination/"
  printf '%s' "$destination"
}

baseline="$(make_case baseline)"
"$CHECKER" "$baseline" >/dev/null

socket_case="$(make_case socket)"
printf '\nimport socket\n' >>"$socket_case/runtime/world_transition_v1/adapter.py"
if "$CHECKER" "$socket_case" >/dev/null 2>&1; then
  echo 'negative fixture unexpectedly accepted a network-capable adapter' >&2
  exit 1
fi

authority_case="$(make_case authority)"
python3 -S - "$authority_case/contracts/world-transition-v1-consumer-lock.json" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["authority"]["completion_signing_performed"] = True
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
if "$CHECKER" "$authority_case" >/dev/null 2>&1; then
  echo 'negative fixture unexpectedly accepted completion signing' >&2
  exit 1
fi

vector_case="$(make_case vector)"
python3 -S - "$vector_case/testdata/world-transition-v1/golden-vectors.json" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["payload_vectors"][0]["expected_sha256"] = "0" * 64
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
if "$CHECKER" "$vector_case" >/dev/null 2>&1; then
  echo 'negative fixture unexpectedly accepted a modified World vector' >&2
  exit 1
fi

shadow_case="$(make_case shadow)"
PYTHONPATH="$shadow_case" python3 -S \
  "$shadow_case/tools/emit_world_transition_v1_shadow_fixture.py" \
  --nakama-revision 1111111111111111111111111111111111111111 \
  --output-dir "$shadow_case/run/world-transition-v1" >/dev/null
python3 -S - "$shadow_case/run/world-transition-v1/nakama-observations.jsonl" <<'PY'
import json
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
first = json.loads(lines[0])
first["next_state_hash"] = "0" * 64
lines[0] = json.dumps(first, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
if PYTHONPATH="$shadow_case" python3 -S -m runtime.world_transition_v1 compare-shadow \
  --world "$shadow_case/run/world-transition-v1/world-observations.jsonl" \
  --candidate "$shadow_case/run/world-transition-v1/nakama-observations.jsonl" \
  --summary "$shadow_case/run/world-transition-v1/tampered-summary.json" \
  >/dev/null 2>&1; then
  echo 'negative fixture unexpectedly accepted a shadow hash divergence' >&2
  exit 1
fi

printf '%s\n' 'TRNM Nakama World transition negative fixtures were rejected.'
