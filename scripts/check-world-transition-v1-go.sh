#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

required=(
  runtime/internal/worldtransition/canonical.go
  runtime/internal/worldtransition/types.go
  runtime/internal/worldtransition/prepare.go
  runtime/internal/worldtransition/verify.go
  runtime/internal/worldtransition/observation.go
  runtime/internal/worldtransition/compare.go
  runtime/internal/worldtransition/contract_test.go
  contracts/world-transition-v1-go-adapter-status.json
  docs/WORLD_TRANSITION_V1_GO_ADAPTER.md
)
for path in "${required[@]}"; do
  test -f "$path" || { echo "ERROR: missing $path" >&2; exit 1; }
done

if rg -n '"(net|net/http|database/sql|crypto/ed25519|os/exec|time|math/rand)"' \
  runtime/internal/worldtransition --glob '*.go'; then
  echo 'ERROR: Go World transition consumer acquired a forbidden capability' >&2
  exit 1
fi

python3 - <<'PY'
import json
from pathlib import Path
status = json.loads(Path('contracts/world-transition-v1-go-adapter-status.json').read_text())
assert status['status'] == 'implemented_pending_exact_head_ci'
assert status['capabilities']['network_io'] is False
assert status['capabilities']['database_io'] is False
assert status['capabilities']['signing'] is False
assert status['authority']['cutover_authorized'] is False
assert status['authority']['public_online_enabled'] is False
assert status['authority']['public_player_market_enabled'] is False
assert 'production_world_command_store_adapter' in status['pending']
PY

unformatted=$(gofmt -l runtime/internal/worldtransition)
test -z "$unformatted" || {
  printf 'ERROR: unformatted Go files:\n%s\n' "$unformatted" >&2
  exit 1
}

(
  cd runtime
  go test ./internal/worldtransition -count=1
  go vet ./internal/worldtransition
)

echo 'Go World transition consumer: PASS'
