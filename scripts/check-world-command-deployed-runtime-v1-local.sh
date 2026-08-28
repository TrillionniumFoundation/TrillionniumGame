#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

bash scripts/check-world-command-deployed-runtime-v1.sh
bash scripts/check-world-command-faultlab-postgres-binding.sh
bash scripts/test-world-command-deployed-runtime-v1-negative.sh

unformatted=$(gofmt -l runtime)
if [[ -n "$unformatted" ]]; then
  printf 'unformatted Go files:\n%s\n' "$unformatted" >&2
  exit 1
fi

(
  cd runtime
  go test ./... -count=1
  go test -race ./internal/worldcommand ./internal/worldtransition -count=1
  go vet ./...
)

python3 -m py_compile scripts/verify-world-command-storage-atomicity.py
node --check scripts/blackbox/world-command-fault.mjs

printf '%s\n' 'World command deployed runtime aggregate source gate: passed'
