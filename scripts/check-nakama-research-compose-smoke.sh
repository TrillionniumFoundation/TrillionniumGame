#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
bash scripts/project-preflight.sh --dev

for command_name in node npm python3 timeout; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "ERROR: required command is missing: $command_name" >&2
    exit 1
  }
done

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "ERROR: Docker and Compose access are required for the Paper Raid smoke gate" >&2
  exit 1
fi
"${docker_cmd[@]}" compose version >/dev/null

tmp=$(mktemp -d)
env_file="$tmp/compose.env"
client_dir="$tmp/client"
state_dir="$tmp/hepta-state"
state_file="$tmp/blackbox-state.json"
control_file="$state_dir/control"
request_log="$state_dir/requests.jsonl"
mkdir -p "$client_dir" "$state_dir"
chmod 700 "$tmp" "$client_dir"
chmod 777 "$state_dir"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  if [[ -s "$env_file" ]]; then
    "${docker_cmd[@]}" compose --env-file "$env_file" -f "$root/compose.yaml" -f "$root/compose.research-smoke.yaml" \
      down -v --remove-orphans --rmi local >/dev/null 2>&1
  fi
  rm -rf "$tmp"
  exit "$status"
}
trap cleanup EXIT INT TERM

http_port=$(python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1])
PY
)

node - "$env_file" "$http_port" "$state_dir" <<'NODE'
import { generateKeyPairSync, randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

const [envFile, httpPort, stateDir] = process.argv.slice(2);
const keyPair = () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  return {
    publicKey: publicKey.export({ type: "spki", format: "der" }).subarray(-32).toString("base64"),
    privateSeed: privateKey.export({ type: "pkcs8", format: "der" }).subarray(-32).toString("base64"),
  };
};
const issuer = keyPair();
const authority = keyPair();
const agents = Array.from({ length: 6 }, keyPair);
const suffix = `${process.pid}-${randomBytes(4).toString("hex")}`;
const secret = () => randomBytes(32).toString("hex");
const issuerKeys = JSON.stringify({ "paper-raid-hepta-v1": issuer.publicKey });
const lines = [
  `TRNM_NAKAMA_COMPOSE_PROJECT=trnm-nakama-paper-raid-${suffix}`,
  `TRNM_NAKAMA_HTTP_PORT=${httpPort}`,
  `TRNM_NAKAMA_IMAGE=trillionnium-nakama:paper-raid-${suffix}`,
  `TRNM_HEPTA_MOCK_IMAGE=trillionnium-hepta-mock:paper-raid-${suffix}`,
  `TRNM_HEPTA_MOCK_STATE_DIR=${stateDir}`,
  `TRNM_NAKAMA_DB_PASSWORD=${secret()}`,
  `NAKAMA_SERVER_KEY=${secret()}`,
  `NAKAMA_SESSION_ENCRYPTION_KEY=${secret()}`,
  `NAKAMA_SESSION_REFRESH_ENCRYPTION_KEY=${secret()}`,
  `NAKAMA_RUNTIME_HTTP_KEY=${secret()}`,
  `NAKAMA_CONSOLE_PASSWORD=${secret()}`,
  `NAKAMA_CONSOLE_SIGNING_KEY=${secret()}`,
  `TRNM_HEPTA_ISSUER_KEYS='${issuerKeys}'`,
  "TRNM_HEPTA_ISSUER_KEY_ID=paper-raid-hepta-v1",
  `TRNM_HEPTA_ISSUER_PRIVATE_SEED=${issuer.privateSeed}`,
  "TRNM_HEPTA_BASE_URL=http://hepta-mock:8080",
  `TRNM_HEPTA_SERVICE_TOKEN=${secret()}`,
  "TRNM_NAKAMA_AUTHORITY_KEY_ID=paper-raid-nakama-v1",
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY=${authority.privateSeed}`,
  `TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY=${authority.publicKey}`,
  `TRNM_NAKAMA_OPERATOR_TOKEN=${secret()}`,
  "TRNM_NAKAMA_MATCH_TICK_RATE=5",
  "TRNM_RESEARCH_TEAM_ID=30000000-0000-4000-8000-000000000001",
  "TRNM_RESEARCH_PAPER_PROJECT_ID=40000000-0000-4000-8000-000000000001",
  "TRNM_RESEARCH_CHALLENGE_ID=50000000-0000-4000-8000-000000000001",
  `TRNM_RESEARCH_SESSION_PREFIX=paper-raid-blackbox-${suffix}`,
  `TRNM_CUSTOM_ID_PREFIX=paper-raid-user-${suffix}`,
  ...agents.slice(0, 5).flatMap((agent, index) => [
    `TRNM_AGENT_${index + 1}_PRIVATE_SEED=${agent.privateSeed}`,
    `TRNM_AGENT_${index + 1}_PUBLIC_KEY=${agent.publicKey}`,
  ]),
  `TRNM_AGENT_ROTATION_PRIVATE_SEED=${agents[5].privateSeed}`,
  `TRNM_AGENT_ROTATION_PUBLIC_KEY=${agents[5].publicKey}`,
  "",
];
writeFileSync(envFile, lines.join("\n"), { mode: 0o600 });
NODE
chmod 600 "$env_file"
printf 'down\n' >"$control_file"
chmod 666 "$control_file"

compose=("${docker_cmd[@]}" compose --env-file "$env_file" -f "$root/compose.yaml" -f "$root/compose.research-smoke.yaml")
if ! "${compose[@]}" config --quiet; then
  echo "ERROR: Paper Raid Compose model is invalid" >&2
  exit 1
fi
if ! "${compose[@]}" up -d --build --wait --wait-timeout 300; then
  echo "ERROR: Paper Raid Compose stack failed to start" >&2
  "${compose[@]}" logs --no-color --tail=240 >&2 || true
  exit 1
fi

nakama_id=$("${compose[@]}" ps -q nakama)
endpoint=$("${compose[@]}" port nakama 7350)
if [[ "$endpoint" == ":0" || "$endpoint" == "127.0.0.1:0" ]]; then
  binding_json=$("${docker_cmd[@]}" inspect -f '{{json (index .HostConfig.PortBindings "7350/tcp")}}' "$nakama_id")
  endpoint=$(python3 - "$binding_json" <<'PY'
import json, sys
bindings = json.loads(sys.argv[1])
if not isinstance(bindings, list) or len(bindings) != 1:
    raise SystemExit(f"unexpected Nakama binding {bindings!r}")
print(f"{bindings[0]['HostIp']}:{bindings[0]['HostPort']}")
PY
  )
fi
if [[ ! "$endpoint" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
  echo "ERROR: Nakama is not published on one loopback port: $endpoint" >&2
  exit 1
fi
nakama_port=${endpoint##*:}

cp scripts/blackbox/package.json scripts/blackbox/package-lock.json scripts/blackbox/research-session-smoke.mjs "$client_dir/"
npm ci --prefix "$client_dir" --ignore-scripts --no-audit --no-fund >/dev/null

run_phase() {
  local phase=$1
  (
    # shellcheck disable=SC1090
    source "$env_file"
    while IFS= read -r variable_name; do export -n "$variable_name"; done < <(compgen -e)
    NAKAMA_HOST=127.0.0.1
    NAKAMA_PORT="$nakama_port"
    BLACKBOX_PHASE="$phase"
    TRNM_BLACKBOX_STATE_FILE="$state_file"
    TRNM_HEPTA_MOCK_CONTROL_FILE="$control_file"
    TRNM_HEPTA_MOCK_LOG_FILE="$request_log"
    export PATH HOME NAKAMA_HOST NAKAMA_PORT BLACKBOX_PHASE
    export NAKAMA_SERVER_KEY NAKAMA_RUNTIME_HTTP_KEY
    export TRNM_RESEARCH_TEAM_ID TRNM_RESEARCH_PAPER_PROJECT_ID TRNM_RESEARCH_CHALLENGE_ID
    export TRNM_RESEARCH_SESSION_PREFIX TRNM_CUSTOM_ID_PREFIX
    case "$phase" in
      health)
        ;;
      prepare3|rotate-complete3|cardinality)
        export TRNM_BLACKBOX_STATE_FILE TRNM_HEPTA_MOCK_CONTROL_FILE TRNM_HEPTA_MOCK_LOG_FILE
        export TRNM_NAKAMA_OPERATOR_TOKEN TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY
        export TRNM_HEPTA_ISSUER_KEY_ID TRNM_HEPTA_ISSUER_PRIVATE_SEED
        export TRNM_AGENT_1_PRIVATE_SEED TRNM_AGENT_2_PRIVATE_SEED TRNM_AGENT_3_PRIVATE_SEED
        export TRNM_AGENT_4_PRIVATE_SEED TRNM_AGENT_5_PRIVATE_SEED TRNM_AGENT_ROTATION_PRIVATE_SEED
        ;;
      recover3)
        export TRNM_BLACKBOX_STATE_FILE TRNM_HEPTA_MOCK_LOG_FILE
        export TRNM_NAKAMA_OPERATOR_TOKEN TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY
        ;;
      *)
        echo "ERROR: unknown Paper Raid black-box phase: $phase" >&2
        exit 64
        ;;
    esac
    timeout 90s node "$client_dir/research-session-smoke.mjs"
  )
}

run_phase health
run_phase prepare3

# Keep PostgreSQL and the Hepta fixture alive, but kill Nakama without a
# graceful termination callback. Durable recovery must create a new fenced
# runtime generation from PostgreSQL.
"${docker_cmd[@]}" kill --signal KILL "$nakama_id" >/dev/null
"${compose[@]}" up -d --no-deps --wait --wait-timeout 300 nakama >/dev/null
printf 'up\n' >"$control_file"
run_phase rotate-complete3

# Completion is now durable locally while Hepta is down. Crash Nakama again,
# then make Hepta return one correctly-shaped but signature-tampered ACK before
# the valid signed ACK. Evidence access must start delivery-only recovery.
nakama_id=$("${compose[@]}" ps -q nakama)
"${docker_cmd[@]}" kill --signal KILL "$nakama_id" >/dev/null
printf 'tamper_completion_once\n' >"$control_file"
"${compose[@]}" up -d --no-deps --wait --wait-timeout 300 nakama >/dev/null
run_phase recover3

printf 'up\n' >"$control_file"
run_phase cardinality

echo "Paper Raid pinned Compose: 3/4/5 keys, epoch rotation, cursor, SIGKILL, exact outbox retry, signed ACK tamper rejection, roots: PASS"
