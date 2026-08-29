#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

baseline=$(mktemp -d)
trap 'rm -rf "$baseline"' EXIT
cp -a runtime/internal/worldtransition "$baseline/worldtransition"
cp contracts/world-transition-v1-go-adapter-status.json "$baseline/status.json"

expect_fail() {
  local label=$1
  shift
  if "$@" >/dev/null 2>&1; then
    echo "ERROR: negative fixture passed: $label" >&2
    exit 1
  fi
}

cp -a "$baseline/worldtransition" runtime/internal/worldtransition.tmp-negative
printf '\npackage worldtransition\nimport _ "net/http"\n' > runtime/internal/worldtransition.tmp-negative/forbidden.go
expect_fail forbidden-network bash -c \
  "rg -n '\"(net|net/http|database/sql|crypto/ed25519|os/exec|time|math/rand)\"' runtime/internal/worldtransition.tmp-negative --glob '*.go'"
rm -rf runtime/internal/worldtransition.tmp-negative

cp "$baseline/status.json" "$baseline/overclaim.json"
python3 - "$baseline/overclaim.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data['authority']['cutover_authorized'] = True
json.dump(data, open(path, 'w'))
PY
expect_fail cutover-overclaim python3 - "$baseline/overclaim.json" <<'PY'
import json, sys
status = json.load(open(sys.argv[1]))
assert status['authority']['cutover_authorized'] is False
PY

cp "$baseline/status.json" "$baseline/store-overclaim.json"
python3 - "$baseline/store-overclaim.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data['pending'].remove('production_world_command_store_adapter')
json.dump(data, open(path, 'w'))
PY
expect_fail store-overclaim python3 - "$baseline/store-overclaim.json" <<'PY'
import json, sys
status = json.load(open(sys.argv[1]))
assert 'production_world_command_store_adapter' in status['pending']
PY

cp runtime/internal/worldtransition/contract_test.go "$baseline/tampered-test.go"
python3 - "$baseline/tampered-test.go" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
text = text.replace('candidate.RequestHash = strings.Repeat("f", 64)', '// divergence removed')
path.write_text(text)
PY
expect_fail missing-divergence rg -q 'candidate\.RequestHash = strings\.Repeat' "$baseline/tampered-test.go"

echo 'Go World transition negative fixtures: PASS'
