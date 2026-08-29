#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

bash scripts/project-preflight.sh --dev
bash scripts/check-nakama-contract.sh
bash scripts/check-nakama-core.sh
bash scripts/check-nakama-restart.sh

if [[ "${TRNM_NAKAMA_SKIP_COMPOSE:-0}" == "1" ]]; then
  echo "ERROR: Compose smoke cannot be skipped for a complete P0 acceptance result" >&2
  exit 2
else
  bash scripts/check-nakama-compose-smoke.sh
fi

echo "nakama-authoritative-match-evidence-v1 gates: PASS"
