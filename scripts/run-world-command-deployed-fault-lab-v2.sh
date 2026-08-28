#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source_script="$root/scripts/run-world-command-deployed-fault-lab.sh"
generated=$(mktemp "$root/scripts/.world-command-deployed-fault-lab.XXXXXX.sh")
cleanup() {
  rm -f "$generated"
}
trap cleanup EXIT INT TERM

python3 - "$source_script" "$generated" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text()
needle = 'exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nakama -d nakama'
replacement = 'exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d trillionnium_nakama'
if source.count(needle) != 1:
    raise SystemExit('fault-lab PostgreSQL command shape drifted; expected exactly one canonical replacement point')
path = pathlib.Path(sys.argv[2])
path.write_text(source.replace(needle, replacement))
path.chmod(0o700)
PY

bash "$generated"
