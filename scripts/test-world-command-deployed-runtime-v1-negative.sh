#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
checker="$root/scripts/check-world-command-deployed-runtime-v1.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

mkdir -p \
  "$work/runtime/internal/core" \
  "$work/runtime/internal/worldcommand" \
  "$work/contracts/world-command-rpc-v1" \
  "$work/contracts" \
  "$work/docs"
cp "$root/runtime/main.go" "$work/runtime/main.go"
cp "$root/runtime/world_authoritative_match.go" "$work/runtime/world_authoritative_match.go"
cp "$root/runtime/world_command_config.go" "$work/runtime/world_command_config.go"
cp "$root/runtime/world_command_http.go" "$work/runtime/world_command_http.go"
cp "$root/runtime/world_command_storage.go" "$work/runtime/world_command_storage.go"
cp "$root/runtime/world_command_rpc.go" "$work/runtime/world_command_rpc.go"
cp "$root/runtime/world_command_failpoint.go" "$work/runtime/world_command_failpoint.go"
cp "$root/runtime/internal/core/world_command.go" "$work/runtime/internal/core/world_command.go"
cp "$root/runtime/internal/worldcommand/coordinator.go" "$work/runtime/internal/worldcommand/coordinator.go"
cp "$root/runtime/internal/worldcommand/store_commit.go" "$work/runtime/internal/worldcommand/store_commit.go"
cp "$root/runtime/internal/worldcommand/atomic_commit_test.go" "$work/runtime/internal/worldcommand/atomic_commit_test.go"
cp "$root/contracts/world-command-deployed-runtime-v1-status.json" "$work/contracts/world-command-deployed-runtime-v1-status.json"
cp -a "$root/contracts/world-command-rpc-v1/." "$work/contracts/world-command-rpc-v1/"
cp "$root/docs/WORLD_COMMAND_DEPLOYED_RUNTIME_V1.md" "$work/docs/WORLD_COMMAND_DEPLOYED_RUNTIME_V1.md"

reset_fixture() {
  rm -rf "$work"
  work=$(mktemp -d)
  mkdir -p \
    "$work/runtime/internal/core" \
    "$work/runtime/internal/worldcommand" \
    "$work/contracts/world-command-rpc-v1" \
    "$work/contracts" \
    "$work/docs"
  cp "$root/runtime/main.go" "$work/runtime/main.go"
  cp "$root/runtime/world_authoritative_match.go" "$work/runtime/world_authoritative_match.go"
  cp "$root/runtime/world_command_config.go" "$work/runtime/world_command_config.go"
  cp "$root/runtime/world_command_http.go" "$work/runtime/world_command_http.go"
  cp "$root/runtime/world_command_storage.go" "$work/runtime/world_command_storage.go"
  cp "$root/runtime/world_command_rpc.go" "$work/runtime/world_command_rpc.go"
  cp "$root/runtime/world_command_failpoint.go" "$work/runtime/world_command_failpoint.go"
  cp "$root/runtime/internal/core/world_command.go" "$work/runtime/internal/core/world_command.go"
  cp "$root/runtime/internal/worldcommand/coordinator.go" "$work/runtime/internal/worldcommand/coordinator.go"
  cp "$root/runtime/internal/worldcommand/store_commit.go" "$work/runtime/internal/worldcommand/store_commit.go"
  cp "$root/runtime/internal/worldcommand/atomic_commit_test.go" "$work/runtime/internal/worldcommand/atomic_commit_test.go"
  cp "$root/contracts/world-command-deployed-runtime-v1-status.json" "$work/contracts/world-command-deployed-runtime-v1-status.json"
  cp -a "$root/contracts/world-command-rpc-v1/." "$work/contracts/world-command-rpc-v1/"
  cp "$root/docs/WORLD_COMMAND_DEPLOYED_RUNTIME_V1.md" "$work/docs/WORLD_COMMAND_DEPLOYED_RUNTIME_V1.md"
}

expect_rejected() {
  local label=$1
  if "$checker" "$work" >/dev/null 2>&1; then
    printf 'negative fixture unexpectedly passed: %s\n' "$label" >&2
    exit 1
  fi
}

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

printf '%s\n' 'World command deployed runtime negative fixtures: rejected as expected'
