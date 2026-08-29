#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKER="$ROOT/scripts/check-world-command-runtime-v1.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/runtime/internal" "$TMP/contracts" "$TMP/docs" "$TMP/scripts" "$TMP/tools"
cp -a "$ROOT/runtime/internal/worldcommand" "$TMP/runtime/internal/worldcommand"
cp "$ROOT/contracts/world-command-runtime-v1-status.json" "$TMP/contracts/"
cp "$ROOT/contracts/world-command-fault-evidence-v1.schema.json" "$TMP/contracts/"
cp "$ROOT/docs/WORLD_COMMAND_RUNTIME_V1.md" "$TMP/docs/"
cp "$CHECKER" "$TMP/scripts/"
cp "$ROOT/tools/summarize_world_command_faults.py" "$TMP/tools/"

cat > "$TMP/runtime/internal/worldcommand/forbidden.go" <<'GO'
package worldcommand
import _ "net/http"
GO
if "$CHECKER" "$TMP" >/dev/null 2>&1; then
  echo 'forbidden network capability was accepted' >&2
  exit 1
fi
rm "$TMP/runtime/internal/worldcommand/forbidden.go"

python3 - "$TMP/contracts/world-command-runtime-v1-status.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
data["authority"]["cutover_authorized"] = True
path.write_text(json.dumps(data, indent=2) + "\n")
PY
if "$CHECKER" "$TMP" >/dev/null 2>&1; then
  echo 'cutover overclaim was accepted' >&2
  exit 1
fi

cat > "$TMP/incomplete-go-test.jsonl" <<'JSONL'
{"Action":"run","Package":"example","Test":"TestResponseLossReusesExactRequestAcrossRestart"}
{"Action":"pass","Package":"example","Test":"TestResponseLossReusesExactRequestAcrossRestart"}
{"Action":"pass","Package":"example"}
JSONL
if python3 "$TMP/tools/summarize_world_command_faults.py" \
  --input "$TMP/incomplete-go-test.jsonl" \
  --output "$TMP/incomplete-summary.json" \
  --commit 1111111111111111111111111111111111111111 \
  --tree 2222222222222222222222222222222222222222 \
  >/dev/null 2>&1; then
  echo 'underpowered fault evidence was accepted' >&2
  exit 1
fi

printf '%s\n' 'world-command-runtime-v1 negative fixtures were rejected'
