#!/usr/bin/env bash
set -uo pipefail

mode=${1:-}
evidence_root=${TRNM_EVIDENCE_ROOT:-pgwire-evidence}
implementation="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ci-pgwire-persistence-adapter-impl.sh"

case "$mode" in
  materialize|postgresql|cockroachdb) ;;
  *)
    printf 'usage: %s {materialize|postgresql|cockroachdb}\n' "$0" >&2
    exit 64
    ;;
esac

set +e
bash "$implementation" "$mode"
implementation_status=$?
set -e

summary="$evidence_root/$mode/summary.json"
if [[ ! -f "$summary" ]]; then
  printf 'pgwire evidence summary is missing: %s\n' "$summary" >&2
  if (( implementation_status == 0 )); then
    exit 1
  fi
  exit "$implementation_status"
fi

python3 - "$summary" "$mode" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected_profile = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid pgwire summary {path}: {exc}")

if payload.get("schema") != "trillionnium.game.pgwire-adapter-ci.v2":
    raise SystemExit("unexpected pgwire summary schema")
if payload.get("profile") != expected_profile:
    raise SystemExit("pgwire summary profile mismatch")
statuses = payload.get("statuses")
if not isinstance(statuses, dict) or not statuses:
    raise SystemExit("pgwire summary statuses are missing")
if any(not isinstance(value, int) for value in statuses.values()):
    raise SystemExit("pgwire summary contains a non-integer status")
computed = all(value == 0 for value in statuses.values())
if payload.get("all_passed") is not computed:
    raise SystemExit("pgwire all_passed does not match recorded statuses")
if not computed:
    failed = sorted(key for key, value in statuses.items() if value != 0)
    raise SystemExit("pgwire contract failed: " + ", ".join(failed))
claims = payload.get("claims") or {}
for forbidden in ("ha_verified", "production_tls_verified", "production_ready"):
    if claims.get(forbidden) is not False:
        raise SystemExit(f"pgwire summary overclaims {forbidden}")
PY
summary_status=$?

if (( summary_status != 0 )); then
  if (( implementation_status != 0 )); then
    exit "$implementation_status"
  fi
  exit "$summary_status"
fi

# The preserved implementation historically ended with a false arithmetic
# expression whose shell status was 1 even when every recorded check passed.
# The canonical summary above is the fail-closed result contract.
exit 0
