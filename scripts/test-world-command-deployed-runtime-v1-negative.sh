#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
checker="$root/scripts/check-world-command-deployed-runtime-v1.sh"
work=""

cleanup() {
  [[ -z "$work" ]] || rm -rf "$work"
}
trap cleanup EXIT INT TERM

reset_fixture() {
  cleanup
  work=$(mktemp -d)
  cp -a "$root/runtime" "$work/runtime"
  cp -a "$root/contracts" "$work/contracts"
  cp -a "$root/docs" "$work/docs"
  cp -a "$root/deploy" "$work/deploy"
  mkdir -p "$work/scripts/blackbox"
  cp "$root/scripts/blackbox/world-command-fault.mjs" "$work/scripts/blackbox/world-command-fault.mjs"
  cp "$root/scripts/run-world-command-deployed-fault-lab.sh" "$work/scripts/run-world-command-deployed-fault-lab.sh"
  cp "$root/scripts/verify-world-command-storage-atomicity.py" "$work/scripts/verify-world-command-storage-atomicity.py"
}

expect_rejected() {
  local label=$1
  if "$checker" "$work" >/dev/null 2>&1; then
    printf 'negative fixture unexpectedly passed: %s\n' "$label" >&2
    exit 1
  fi
}

reset_fixture
python3 - "$work/contracts/world-command-deployed-runtime-v1-status.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
data["authority"]["cutover_authorized"] = True
path.write_text(json.dumps(data, indent=2) + "\n")
PY
expect_rejected 'cutover overclaim'

reset_fixture
python3 - "$work/contracts/world-command-rpc-v1/ready-response.schema.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
data["properties"]["external_execution_under_lock"]["const"] = True
path.write_text(json.dumps(data, indent=2) + "\n")
PY
expect_rejected 'external execution under lock'

reset_fixture
python3 - "$work/runtime/world_command_http.go" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text().replace("tls.VersionTLS13", "tls.VersionTLS12", 1)
path.write_text(text)
PY
expect_rejected 'TLS downgrade'

reset_fixture
python3 - "$work/runtime/world_command_storage.go" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text().replace("worldCommandStorageCollection", "matchStorageCollection", 1)
path.write_text(text)
PY
expect_rejected 'atomic World sidecar removal'

reset_fixture
python3 - "$work/runtime/internal/worldcommand/coordinator.go" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
prepare = text.index("c.Store.Prepare")
execute = text.index("c.Executor.Execute")
text = text[:prepare] + text[execute:execute+len("c.Executor.Execute")] + text[prepare+len("c.Store.Prepare"):execute] + "c.Store.Prepare" + text[execute+len("c.Executor.Execute"):]
path.write_text(text)
PY
expect_rejected 'execute-before-prepare ordering'

reset_fixture
cat >> "$work/runtime/world_command_http.go" <<'GO'

func forbiddenProcessExit() { os.Exit(1) }
GO
expect_rejected 'process exit outside isolated failpoint'

reset_fixture
cat >> "$work/runtime/world_authoritative_match.go" <<'GO'

const automaticFallbackToLegacy = true
GO
expect_rejected 'target automatic legacy fallback'

reset_fixture
python3 - "$work/runtime/world_command_config.go" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text().replace("envWorldChallengeHash", "envWorldInitialStateHash", 1)
path.write_text(text)
PY
expect_rejected 'challenge snapshot conflated with initial state'

printf '%s\n' 'World command deployed runtime negative fixtures: rejected as expected'
