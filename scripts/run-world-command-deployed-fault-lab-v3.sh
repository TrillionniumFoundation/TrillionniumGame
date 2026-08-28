#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source_script="$root/scripts/run-world-command-deployed-fault-lab.sh"
generated=$(mktemp "$root/scripts/.world-command-deployed-fault-lab.XXXXXX.sh")
cleanup() {
  rm -f "$generated"
}
trap cleanup EXIT INT TERM

if [[ "${TRNM_WORLD_FIXTURE_PREBUILT:-0}" == "1" ]]; then
  : "${TRNM_WORLD_FIXTURE_IMAGE:?TRNM_WORLD_FIXTURE_IMAGE is required when TRNM_WORLD_FIXTURE_PREBUILT=1}"
  docker image inspect "$TRNM_WORLD_FIXTURE_IMAGE" >/dev/null
fi

python3 - "$source_script" "$generated" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text()
postgres_needle = 'exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nakama -d nakama'
postgres_replacement = 'exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d trillionnium_nakama'
build_needle = '"$compose" build'
build_replacement = '''if [[ "${TRNM_WORLD_FIXTURE_PREBUILT:-0}" == "1" ]]; then
  "$compose" build nakama tls-fixture response-drop-proxy
else
  "$compose" build
fi'''

if source.count(postgres_needle) != 1:
    raise SystemExit('fault-lab PostgreSQL command shape drifted; expected exactly one replacement point')
if source.count(build_needle) != 1:
    raise SystemExit('fault-lab build command shape drifted; expected exactly one replacement point')

path = pathlib.Path(sys.argv[2])
path.write_text(
    source.replace(postgres_needle, postgres_replacement).replace(build_needle, build_replacement)
)
path.chmod(0o700)
PY

bash "$generated"
