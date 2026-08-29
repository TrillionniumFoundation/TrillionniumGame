#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

for command_name in node npm python3 curl timeout sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'ERROR: required command is missing: %s\n' "$command_name" >&2
    exit 1
  }
done

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo 'ERROR: Docker and Compose access are required for the deployed fault lab' >&2
  exit 1
fi
"${docker_cmd[@]}" compose version >/dev/null

tmp=$(mktemp -d)
env_file="$tmp/fault-lab.env"
client_dir="$tmp/client"
ca_file="$tmp/ca.pem"
evidence_dir=${TRNM_WORLD_COMMAND_EVIDENCE_DIR:-"$root/run/world-command-deployed-runtime-v1"}
rm -rf "$evidence_dir"
mkdir -p "$client_dir" "$evidence_dir/scenarios"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  if [[ -s "$env_file" ]]; then
    "${compose[@]}" logs --no-color >"$evidence_dir/compose.log" 2>&1 || true
    "${compose[@]}" ps -a >"$evidence_dir/compose-ps.txt" 2>&1 || true
    if [[ "${TRNM_KEEP_WORLD_COMMAND_FAULT_LAB:-0}" != "1" ]]; then
      "${compose[@]}" down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
    fi
  fi
  rm -rf "$tmp"
  exit "$status"
}
trap cleanup EXIT INT TERM

free_port() {
  python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
}

nakama_port=$(free_port)
world_port=$(free_port)
proxy_port=$(free_port)

node - "$env_file" "$nakama_port" "$world_port" "$proxy_port" <<'NODE'
import { createHash, generateKeyPairSync, randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

const [envFile, nakamaPort, worldPort, proxyPort] = process.argv.slice(2);
const keyPair = () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  return {
    publicKey: publicKey.export({ type: "spki", format: "der" }).subarray(-32).toString("base64"),
    privateSeed: privateKey.export({ type: "pkcs8", format: "der" }).subarray(-32).toString("base64"),
  };
};
const digest = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const issuer = keyPair();
const authority = keyPair();
const agentOne = keyPair();
const agentTwo = keyPair();
const suffix = `${process.pid}-${randomBytes(5).toString("hex")}`;
const random = () => randomBytes(32).toString("hex");
const initialState = Buffer.from('{"counter":0}', "utf8");
const issuerKeys = JSON.stringify({ "blackbox-hepta-v1": issuer.publicKey });
const lines = [
  `TRNM_NAKAMA_COMPOSE_PROJECT=trnm-game-world-fault-${suffix}`,
  `TRNM_NAKAMA_HTTP_PORT=${nakamaPort}`,
  `TRNM_WORLD_FIXTURE_PORT=${worldPort}`,
  `TRNM_FAULT_PROXY_PORT=${proxyPort}`,
  `TRNM_NAKAMA_IMAGE=trillionnium-game:world-fault-${suffix}`,
  `TRNM_NAKAMA_DB_PASSWORD=${random()}`,
  `NAKAMA_SERVER_KEY=${random()}`,
  `NAKAMA_SESSION_ENCRYPTION_KEY=${random()}`,
  `NAKAMA_SESSION_REFRESH_ENCRYPTION_KEY=${random()}`,
  `NAKAMA_RUNTIME_HTTP_KEY=${random()}`,
  `NAKAMA_CONSOLE_PASSWORD=${random()}`,
  `NAKAMA_CONSOLE_SIGNING_KEY=${random()}`,
  `TRNM_HEPTA_ISSUER_KEYS='${issuerKeys}'`,
  "TRNM_HEPTA_ISSUER_KEY_ID=blackbox-hepta-v1",
  `TRNM_HEPTA_ISSUER_PRIVATE_SEED=${issuer.privateSeed}`,
  "TRNM_NAKAMA_AUTHORITY_KEY_ID=blackbox-nakama-v1",
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY=${authority.privateSeed}`,
  `TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY=${authority.publicKey}`,
  `TRNM_NAKAMA_OPERATOR_TOKEN=${random()}`,
  `TRNM_AGENT_ONE_PRIVATE_SEED=${agentOne.privateSeed}`,
  `TRNM_AGENT_ONE_PUBLIC_KEY=${agentOne.publicKey}`,
  `TRNM_AGENT_TWO_PRIVATE_SEED=${agentTwo.privateSeed}`,
  `TRNM_AGENT_TWO_PUBLIC_KEY=${agentTwo.publicKey}`,
  `TRNM_WORLD_FIXTURE_BEARER_TOKEN=${random()}`,
  `TRNM_RESPONSE_PROXY_CONTROL_TOKEN=${random()}`,
  "TRNM_WORLD_RULESET_REVISION=blackbox-ruleset-v1",
  "TRNM_WORLD_CONTENT_REVISION=blackbox-content-v1",
  "TRNM_WORLD_STATE_SCHEMA_ID=trnm.blackbox.state.v1",
  "TRNM_WORLD_COMMAND_SCHEMA_ID=trnm.blackbox.move.v1",
  `TRNM_WORLD_INITIAL_STATE_JSON_BASE64=${initialState.toString("base64")}`,
  "TRNM_WORLD_INITIAL_TICK=0",
  `TRNM_WORLD_RULESET_HASH=${digest(Buffer.from("blackbox-ruleset-v1", "utf8"))}`,
  `TRNM_WORLD_CONTENT_HASH=${digest(Buffer.from("blackbox-dataset-v1", "utf8"))}`,
  `TRNM_WORLD_INITIAL_STATE_HASH=${digest(initialState)}`,
  `TRNM_WORLD_CHALLENGE_SNAPSHOT_HASH=${digest(Buffer.from("blackbox-challenge-snapshot-v1", "utf8"))}`,
  "TRNM_WORLD_TRANSITION_TIMEOUT_MS=15000",
  "TRNM_WORLD_TRANSITION_MAX_RESPONSE_BYTES=4194304",
  "TRNM_WORLD_COMMAND_FAILPOINT=",
  `TRNM_BLACKBOX_CUSTOM_ID_ONE=unused-a-${suffix}`,
  `TRNM_BLACKBOX_CUSTOM_ID_TWO=unused-b-${suffix}`,
  `TRNM_BLACKBOX_LOGICAL_MATCH_ID=unused-match-${suffix}`,
  "TRNM_NAKAMA_MATCH_TICK_RATE=5",
  "",
];
writeFileSync(envFile, lines.join("\n"), { mode: 0o600 });
NODE
chmod 600 "$env_file"

export TRNM_NAKAMA_HTTP_PORT="$nakama_port"
export TRNM_WORLD_FIXTURE_PORT="$world_port"
export TRNM_FAULT_PROXY_PORT="$proxy_port"
if [[ "${docker_cmd[0]}" == "sudo" ]]; then
  compose=(
    sudo -n env
    "TRNM_NAKAMA_HTTP_PORT=$nakama_port"
    "TRNM_WORLD_FIXTURE_PORT=$world_port"
    "TRNM_FAULT_PROXY_PORT=$proxy_port"
    docker compose
    --env-file "$env_file"
    -f "$root/compose.yaml"
    -f "$root/deploy/world-command-fault-lab/compose.yaml"
  )
else
  compose=(
    "${docker_cmd[@]}" compose
    --env-file "$env_file"
    -f "$root/compose.yaml"
    -f "$root/deploy/world-command-fault-lab/compose.yaml"
  )
fi

set_env() {
  local key=$1
  local value=$2
  python3 - "$env_file" "$key" "$value" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
key, value = sys.argv[2], sys.argv[3]
lines = [line for line in path.read_text().splitlines() if not line.startswith(key + "=")]
lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")
PY
  chmod 600 "$env_file"
}

env_value() {
  sed -n "s/^$1=//p" "$env_file" | tail -1
}

"${compose[@]}" config --format json >"$evidence_dir/rendered-compose.json"
python3 - "$evidence_dir/rendered-compose.json" <<'PY'
import json, sys
model = json.load(open(sys.argv[1]))
services = model.get("services", {})
required = {"postgres", "nakama", "tls-init", "world-fixture", "response-drop-proxy"}
if not required.issubset(services):
    raise SystemExit(f"missing services: {sorted(required - set(services))}")
for service in ("world-fixture", "response-drop-proxy"):
    ports = services[service].get("ports", [])
    if len(ports) != 1 or ports[0].get("host_ip") != "127.0.0.1":
        raise SystemExit(f"{service} is not loopback-only: {ports}")
if services["postgres"].get("ports"):
    raise SystemExit("PostgreSQL unexpectedly publishes a host port")
if services["nakama"].get("restart") not in ("no", "none"):
    raise SystemExit("Nakama fault-lab process exits would be hidden by restart policy")
PY

cp scripts/blackbox/package.json scripts/blackbox/package-lock.json scripts/blackbox/world-command-fault.mjs "$client_dir/"
npm ci --prefix "$client_dir" --ignore-scripts --no-audit --no-fund >/dev/null

if ! "${compose[@]}" up -d --build --wait --wait-timeout 420; then
  echo 'ERROR: deployed World command fault-lab stack failed to start' >&2
  "${compose[@]}" logs --no-color --tail=240 >&2 || true
  exit 1
fi
"${compose[@]}" cp tls-init:/certs/ca.pem "$ca_file" >/dev/null
chmod 600 "$ca_file"

source_env_allowlist() {
  # shellcheck disable=SC1090
  source "$env_file"
  while IFS= read -r variable_name; do
    export -n "$variable_name"
  done < <(compgen -e)
  export PATH HOME
}

run_client() {
  local phase=$1
  local state_file=${2:-}
  local command_id=${3:-}
  local expected=${4:-event}
  local resume=${5:-0}
  (
    source_env_allowlist
    NAKAMA_HOST=127.0.0.1
    NAKAMA_PORT="$nakama_port"
    BLACKBOX_PHASE="$phase"
    TRNM_BLACKBOX_STATE_FILE="$state_file"
    TRNM_FAULT_COMMAND_ID="$command_id"
    EXPECT_COMMAND_OUTCOME="$expected"
    TRNM_FAULT_RESUME="$resume"
    export NAKAMA_HOST NAKAMA_PORT BLACKBOX_PHASE TRNM_BLACKBOX_STATE_FILE
    export NAKAMA_SERVER_KEY NAKAMA_RUNTIME_HTTP_KEY
    case "$phase" in
      ready)
        ;;
      seed)
        export TRNM_NAKAMA_OPERATOR_TOKEN
        export TRNM_HEPTA_ISSUER_KEY_ID TRNM_HEPTA_ISSUER_PRIVATE_SEED
        export TRNM_AGENT_ONE_PRIVATE_SEED TRNM_AGENT_TWO_PRIVATE_SEED
        export TRNM_BLACKBOX_CUSTOM_ID_ONE TRNM_BLACKBOX_CUSTOM_ID_TWO TRNM_BLACKBOX_LOGICAL_MATCH_ID
        ;;
      send)
        export TRNM_NAKAMA_OPERATOR_TOKEN TRNM_AGENT_ONE_PRIVATE_SEED TRNM_AGENT_TWO_PRIVATE_SEED
        export TRNM_WORLD_COMMAND_SCHEMA_ID TRNM_FAULT_COMMAND_ID EXPECT_COMMAND_OUTCOME TRNM_FAULT_RESUME
        TRNM_FAULT_COMMAND_JSON='{"delta":1,"kind":"advance"}'
        export TRNM_FAULT_COMMAND_JSON
        ;;
      status)
        export TRNM_NAKAMA_OPERATOR_TOKEN
        ;;
      *)
        echo "unknown client phase: $phase" >&2
        exit 64
        ;;
    esac
    timeout 45s node "$client_dir/world-command-fault.mjs"
  )
}

wait_ready() {
  local output=""
  for _ in $(seq 1 45); do
    if output=$(run_client ready 2>&1); then
      printf '%s\n' "$output"
      return 0
    fi
    sleep 1
  done
  printf '%s\n' "$output" >&2
  return 1
}

for endpoint in \
  "https://127.0.0.1:${world_port}/healthz" \
  "https://127.0.0.1:${proxy_port}/healthz"; do
  for _ in $(seq 1 30); do
    if curl --silent --show-error --fail --cacert "$ca_file" "$endpoint" >/dev/null; then
      break
    fi
    sleep 1
  done
  curl --silent --show-error --fail --cacert "$ca_file" "$endpoint" >/dev/null
 done
wait_ready >"$evidence_dir/initial-ready.jsonl"

proxy_control() {
  local mode=$1
  local delay_millis=${2:-0}
  local token
  token=$(env_value TRNM_RESPONSE_PROXY_CONTROL_TOKEN)
  local payload
  if [[ "$mode" == "delay_next" ]]; then
    payload=$(printf '{"mode":"delay_next","delay_millis":%d}' "$delay_millis")
  else
    payload=$(printf '{"mode":"%s"}' "$mode")
  fi
  curl --silent --show-error --fail --cacert "$ca_file" \
    -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    "https://127.0.0.1:${proxy_port}/control"
}

proxy_stats() {
  local token
  token=$(env_value TRNM_RESPONSE_PROXY_CONTROL_TOKEN)
  curl --silent --show-error --fail --cacert "$ca_file" \
    -H "Authorization: Bearer $token" \
    "https://127.0.0.1:${proxy_port}/stats"
}

world_stats() {
  local token
  token=$(env_value TRNM_WORLD_FIXTURE_BEARER_TOKEN)
  curl --silent --show-error --fail --cacert "$ca_file" \
    -H "Authorization: Bearer $token" \
    "https://127.0.0.1:${world_port}/v1/stats"
}

set_scenario() {
  local name=$1
  local suffix
  suffix=$(env_value TRNM_NAKAMA_COMPOSE_PROJECT)
  set_env TRNM_BLACKBOX_CUSTOM_ID_ONE "fault-${name}-a-${suffix}"
  set_env TRNM_BLACKBOX_CUSTOM_ID_TWO "fault-${name}-b-${suffix}"
  set_env TRNM_BLACKBOX_LOGICAL_MATCH_ID "fault-${name}-${suffix}"
}

scenario_state() {
  printf '%s/scenarios/%s/client-state.json' "$evidence_dir" "$1"
}

scenario_dir() {
  local directory="$evidence_dir/scenarios/$1"
  mkdir -p "$directory"
  printf '%s' "$directory"
}

postgres_query() {
  "${compose[@]}" exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nakama -d nakama "$@"
}

dump_atomicity() {
  local name=$1
  local command_id=$2
  local state_file
  state_file=$(scenario_state "$name")
  local directory
  directory=$(scenario_dir "$name")
  local logical_match_id
  logical_match_id=$(python3 - "$state_file" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["logical_match_id"])
PY
)
  postgres_query -Atc "SELECT value::text FROM storage WHERE collection='trnm_authoritative_match_v1' AND key='${logical_match_id}' ORDER BY update_time DESC LIMIT 1" >"$directory/core-storage.json"
  postgres_query -Atc "SELECT value::text FROM storage WHERE collection='trnm_world_command_v1' AND key='${logical_match_id}' ORDER BY update_time DESC LIMIT 1" >"$directory/world-storage.json"
  [[ -s "$directory/core-storage.json" && -s "$directory/world-storage.json" ]] || {
    echo "ERROR: atomic storage objects are missing for $name" >&2
    exit 1
  }
  python3 scripts/verify-world-command-storage-atomicity.py \
    --core "$directory/core-storage.json" \
    --world "$directory/world-storage.json" \
    --logical-match-id "$logical_match_id" \
    --expected-command-id "$command_id" \
    --output "$directory/atomicity.json" \
    >"$directory/atomicity.stdout.json"
}

recreate_nakama() {
  "${compose[@]}" up -d --no-deps --force-recreate nakama >/dev/null
  wait_ready >/dev/null
}

wait_nakama_exit() {
  local expected=$1
  local container
  container=$("${compose[@]}" ps -a -q nakama)
  [[ -n "$container" ]] || { echo 'ERROR: Nakama container is absent' >&2; exit 1; }
  for _ in $(seq 1 80); do
    status=$("${docker_cmd[@]}" inspect -f '{{.State.Status}}' "$container")
    if [[ "$status" == "exited" ]]; then
      code=$("${docker_cmd[@]}" inspect -f '{{.State.ExitCode}}' "$container")
      [[ "$code" == "$expected" ]] || {
        echo "ERROR: Nakama exited with $code, expected $expected" >&2
        exit 1
      }
      printf '%s\n' "$code"
      return 0
    fi
    sleep 0.25
  done
  echo 'ERROR: Nakama did not reach the expected process failpoint' >&2
  exit 1
}

run_happy() {
  local name=happy
  local directory
  directory=$(scenario_dir "$name")
  local state
  state=$(scenario_state "$name")
  local command_id=fault-happy-command-v1
  set_scenario "$name"
  run_client seed "$state" >"$directory/seed.jsonl"
  run_client send "$state" "$command_id" event 0 >"$directory/send.jsonl"
  run_client status "$state" >"$directory/status.jsonl"
  dump_atomicity "$name" "$command_id"
  printf '{"scenario":"happy","passed":true}\n' >"$directory/result.json"
}

run_response_loss() {
  local name=response-loss
  local directory
  directory=$(scenario_dir "$name")
  local state
  state=$(scenario_state "$name")
  local command_id=fault-response-loss-command-v1
  set_scenario "$name"
  run_client seed "$state" >"$directory/seed.jsonl"
  proxy_control drop_next >"$directory/proxy-control.json"
  run_client send "$state" "$command_id" error 0 >"$directory/first-send.jsonl"
  run_client status "$state" >"$directory/pending-status.jsonl"
  run_client send "$state" "$command_id" event 0 >"$directory/retry-send.jsonl"
  run_client status "$state" >"$directory/final-status.jsonl"
  proxy_stats >"$directory/proxy-stats.json"
  world_stats >"$directory/world-stats.json"
  python3 - "$directory/proxy-stats.json" "$directory/world-stats.json" <<'PY'
import json, sys
proxy = json.load(open(sys.argv[1]))
world = json.load(open(sys.argv[2]))
if proxy.get("dropped", 0) < 1:
    raise SystemExit("proxy did not drop a post-upstream response")
if world.get("cache_hits", 0) < 1:
    raise SystemExit("retry did not reuse the durable World request result")
PY
  dump_atomicity "$name" "$command_id"
  printf '{"scenario":"response_loss","passed":true}\n' >"$directory/result.json"
}

run_external_wait_lock_check() {
  local name=external-wait
  local directory
  directory=$(scenario_dir "$name")
  local state
  state=$(scenario_state "$name")
  local command_id=fault-external-wait-command-v1
  set_scenario "$name"
  run_client seed "$state" >"$directory/seed.jsonl"
  proxy_control delay_next 7000 >"$directory/proxy-control.json"
  run_client send "$state" "$command_id" event 0 >"$directory/send.jsonl" 2>"$directory/send.stderr" &
  local client_pid=$!
  sleep 2
  postgres_query -Atc "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND xact_start IS NOT NULL AND now()-xact_start > interval '1 second'" >"$directory/long-transactions.txt"
  postgres_query -P pager=off -c "SELECT pid,state,xact_start,wait_event_type,wait_event,left(query,240) AS query FROM pg_stat_activity WHERE datname=current_database() ORDER BY pid" >"$directory/pg-stat-activity.txt"
  postgres_query -P pager=off -c "SELECT l.pid,l.locktype,l.mode,l.granted,l.relation::regclass AS relation,a.state,a.xact_start,left(a.query,160) AS query FROM pg_locks l LEFT JOIN pg_stat_activity a ON a.pid=l.pid WHERE a.datname=current_database() ORDER BY l.pid,l.locktype,l.mode" >"$directory/pg-locks.txt"
  [[ "$(tr -d '[:space:]' <"$directory/long-transactions.txt")" == "0" ]] || {
    echo 'ERROR: Game held a PostgreSQL transaction during external World wait' >&2
    cat "$directory/pg-stat-activity.txt" >&2
    exit 1
  }
  wait "$client_pid"
  dump_atomicity "$name" "$command_id"
  printf '{"scenario":"external_wait_without_long_transaction","passed":true}\n' >"$directory/result.json"
}

run_process_failpoint() {
  local name=$1
  local failpoint=$2
  local exit_code=$3
  local command_id=$4
  local directory
  directory=$(scenario_dir "$name")
  local state
  state=$(scenario_state "$name")

  set_env TRNM_WORLD_COMMAND_FAILPOINT "$failpoint"
  recreate_nakama
  set_scenario "$name"
  run_client seed "$state" >"$directory/seed.jsonl"
  run_client send "$state" "$command_id" disconnect 0 >"$directory/failed-send.jsonl" 2>"$directory/failed-send.stderr" || true
  wait_nakama_exit "$exit_code" >"$directory/process-exit-code.txt"

  set_env TRNM_WORLD_COMMAND_FAILPOINT ""
  recreate_nakama
  run_client send "$state" "$command_id" event 1 >"$directory/recovered-send.jsonl"
  run_client status "$state" >"$directory/status.jsonl"
  dump_atomicity "$name" "$command_id"
  printf '{"scenario":"%s","passed":true,"exit_code":%d}\n' "$name" "$exit_code" >"$directory/result.json"
}

run_happy
run_response_loss
run_external_wait_lock_check
run_process_failpoint after-reservation after_reservation 85 fault-after-reservation-command-v1
run_process_failpoint after-verify after_verify 86 fault-after-verify-command-v1

proxy_stats >"$evidence_dir/final-proxy-stats.json"
world_stats >"$evidence_dir/final-world-stats.json"
postgres_query -P pager=off -c "SELECT collection,key,version,create_time,update_time FROM storage WHERE collection IN ('trnm_authoritative_match_v1','trnm_world_command_v1') ORDER BY collection,key" >"$evidence_dir/storage-index.txt"

python3 - "$root" "$evidence_dir" <<'PY'
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
evidence = pathlib.Path(sys.argv[2])
scenario_names = ["happy", "response-loss", "external-wait", "after-reservation", "after-verify"]
scenarios = []
for name in scenario_names:
    result = json.loads((evidence / "scenarios" / name / "result.json").read_text())
    if result.get("passed") is not True:
        raise SystemExit(f"scenario failed: {name}")
    atomicity = json.loads((evidence / "scenarios" / name / "atomicity.json").read_text())
    if atomicity.get("atomicity_verified") is not True:
        raise SystemExit(f"atomicity not verified: {name}")
    scenarios.append({"id": name, "result": result, "atomicity": atomicity})
head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
tree = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip()
report = {
    "contract_version": "trnm_game_world_command_deployed_fault_report_v1",
    "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "repository": "TrillionniumFoundation/TrillionniumGame",
    "repository_id": 1323087470,
    "commit": head,
    "tree": tree,
    "environment": "isolated_single_host_postgresql_nakama_https_world_fixture",
    "scenarios": scenarios,
    "scenario_count": len(scenarios),
    "all_passed": True,
    "authority": {
        "cutover_authorized": False,
        "closed_online_promoted": False,
        "public_online_enabled": False,
        "public_player_market_enabled": False,
    },
    "limitations": [
        "single physical host",
        "deterministic fixture World implementation rather than a production World deployment",
        "short bounded fault matrix rather than 24-hour endurance",
        "no authority cutover or public admission",
        "no value settlement",
    ],
}
(evidence / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
PY

find "$evidence_dir" -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  >"$evidence_dir/SHA256SUMS"

printf 'World command deployed fault lab: PASS (%s)\n' "$evidence_dir"
