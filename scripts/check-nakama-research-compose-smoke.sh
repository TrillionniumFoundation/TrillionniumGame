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
failpoint_file="$state_dir/nakama-failpoint"
failpoint_reached="$failpoint_file.reached"
request_log="$state_dir/requests.jsonl"
mkdir -p "$client_dir" "$state_dir"
chmod 700 "$tmp" "$client_dir"
chmod 777 "$state_dir"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  if [[ -s "$env_file" ]]; then
    if (( status != 0 )); then
      "${docker_cmd[@]}" compose --env-file "$env_file" -f "$root/compose.yaml" -f "$root/compose.research-smoke.yaml" \
        logs --no-color --tail=240 nakama >&2 || true
    fi
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

source_revision=$(git rev-parse HEAD)
source_tree=$(git rev-parse 'HEAD^{tree}')
sbom_sha256=$(sha256sum runtime/sbom.cdx.json | cut -d' ' -f1)
node - "$env_file" "$http_port" "$state_dir" "$source_revision" "$source_tree" "$sbom_sha256" <<'NODE'
import { generateKeyPairSync, randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

const [envFile, httpPort, stateDir, sourceRevision, sourceTree, sbomSha256] = process.argv.slice(2);
const keyPair = () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  return {
    publicKey: publicKey.export({ type: "spki", format: "der" }).subarray(-32).toString("base64"),
    privateSeed: privateKey.export({ type: "pkcs8", format: "der" }).subarray(-32).toString("base64"),
  };
};
const issuer = keyPair();
const controlIssuer = keyPair();
const authority = keyPair();
const agents = Array.from({ length: 6 }, keyPair);
const suffix = `${process.pid}-${randomBytes(4).toString("hex")}`;
const secret = () => randomBytes(32).toString("hex");
const issuerKeys = JSON.stringify({ "paper-raid-hepta-v1": issuer.publicKey });
const controlIssuerKeys = JSON.stringify({ "paper-raid-hepta-control-v2": controlIssuer.publicKey });
const lines = [
  `TRNM_NAKAMA_COMPOSE_PROJECT=trnm-nakama-paper-raid-${suffix}`,
  `TRNM_NAKAMA_HTTP_PORT=${httpPort}`,
  `TRNM_NAKAMA_IMAGE=trillionnium-nakama:paper-raid-${suffix}`,
  `TRNM_NAKAMA_SOURCE_REVISION=${sourceRevision}`,
  `TRNM_NAKAMA_SOURCE_TREE=${sourceTree}`,
  `TRNM_NAKAMA_SBOM_SHA256=${sbomSha256}`,
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
  `TRNM_HEPTA_CONTROL_ISSUER_KEYS='${controlIssuerKeys}'`,
  "TRNM_HEPTA_CONTROL_ISSUER_KEY_ID=paper-raid-hepta-control-v2",
  `TRNM_HEPTA_CONTROL_PRIVATE_SEED=${controlIssuer.privateSeed}`,
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
printf '\n' >"$failpoint_file"
chmod 666 "$failpoint_file"
printf '\n' >"$failpoint_reached"
chmod 666 "$failpoint_reached"

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
      create-pending3|create-recover3|resume-pending3|resume-recover-replace-pending3|replace-recover-complete-pending3|complete-recover3|cardinality)
        export TRNM_BLACKBOX_STATE_FILE TRNM_HEPTA_MOCK_CONTROL_FILE TRNM_HEPTA_MOCK_LOG_FILE
        export TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY
        export TRNM_HEPTA_ISSUER_KEY_ID TRNM_HEPTA_ISSUER_PRIVATE_SEED
        export TRNM_HEPTA_CONTROL_ISSUER_KEY_ID TRNM_HEPTA_CONTROL_PRIVATE_SEED
        export TRNM_AGENT_1_PRIVATE_SEED TRNM_AGENT_2_PRIVATE_SEED TRNM_AGENT_3_PRIVATE_SEED
        export TRNM_AGENT_4_PRIVATE_SEED TRNM_AGENT_5_PRIVATE_SEED TRNM_AGENT_ROTATION_PRIVATE_SEED
        ;;
      recover3)
        export TRNM_BLACKBOX_STATE_FILE TRNM_HEPTA_MOCK_LOG_FILE
        export TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY
        ;;
      *)
        echo "ERROR: unknown Paper Raid black-box phase: $phase" >&2
        exit 64
        ;;
    esac
    timeout 90s node "$client_dir/research-session-smoke.mjs"
  )
}

set_failpoint() {
  local stage=$1
  local command_id=$2
  printf '\n' >"$failpoint_reached"
  chmod 666 "$failpoint_reached"
  printf '%s:%s\n' "$stage" "$command_id" >"$failpoint_file"
}

clear_failpoint() {
  printf '\n' >"$failpoint_file"
  printf '\n' >"$failpoint_reached"
}

wait_for_failpoint() {
  local expected=$1
  local deadline=$((SECONDS + 45))
  while (( SECONDS < deadline )); do
    if [[ -f "$failpoint_reached" ]] && [[ $(tr -d '\r\n' <"$failpoint_reached") == "$expected" ]]; then
      return 0
    fi
    sleep 0.1
  done
  echo "ERROR: timed out waiting for Nakama control failpoint $expected" >&2
  "${compose[@]}" logs --no-color --tail=240 nakama >&2 || true
  return 1
}

kill_nakama() {
  nakama_id=$("${compose[@]}" ps -q nakama)
  "${docker_cmd[@]}" kill --signal KILL "$nakama_id" >/dev/null
}

start_nakama() {
  "${compose[@]}" up -d --no-deps --wait --wait-timeout 300 nakama >/dev/null
}

crash_blocked_phase() {
  local phase=$1
  local stage=$2
  local command_id=$3
  local expected="$stage:$command_id"
  set_failpoint "$stage" "$command_id"
  run_phase "$phase" &
  local client_pid=$!
  wait_for_failpoint "$expected"
  kill_nakama
  if wait "$client_pid"; then
    echo "ERROR: blocked phase $phase returned successfully before SIGKILL" >&2
    exit 1
  fi
  clear_failpoint
  start_nakama
}

run_phase health
crash_blocked_phase create-pending3 create_after_runtime 90000000-0000-4000-8000-000000000301
run_phase create-recover3

# A normal hard process loss removes the live runtime. The signed resume then
# creates a new generation and is killed after that identity is durable but
# before the applied command receipt is persisted.
kill_nakama
set_failpoint resume_after_runtime 90000000-0000-4000-8000-000000000302
start_nakama
run_phase resume-pending3 &
resume_client_pid=$!
wait_for_failpoint "resume_after_runtime:90000000-0000-4000-8000-000000000302"
kill_nakama
if wait "$resume_client_pid"; then
  echo "ERROR: blocked signed resume returned successfully before SIGKILL" >&2
  exit 1
fi
clear_failpoint
start_nakama

# Recover the exact signed resume, then reserve replacement and die before its
# internal signal. After another signed resume, retry replacement atomically,
# execute epoch-two work, reserve completion, and die in the same signal gap.
set_failpoint replace_roster_before_signal 90000000-0000-4000-8000-000000000303
run_phase resume-recover-replace-pending3 &
replace_client_pid=$!
wait_for_failpoint "replace_roster_before_signal:90000000-0000-4000-8000-000000000303"
kill_nakama
if wait "$replace_client_pid"; then
  echo "ERROR: blocked signed replacement returned successfully before SIGKILL" >&2
  exit 1
fi
clear_failpoint
start_nakama

set_failpoint complete_before_signal 90000000-0000-4000-8000-000000000304
run_phase replace-recover-complete-pending3 &
complete_client_pid=$!
wait_for_failpoint "complete_before_signal:90000000-0000-4000-8000-000000000304"
kill_nakama
if wait "$complete_client_pid"; then
  echo "ERROR: blocked signed completion returned successfully before SIGKILL" >&2
  exit 1
fi
clear_failpoint
start_nakama
run_phase complete-recover3

# Completion is now durable locally while Hepta is down. Crash Nakama again,
# then make Hepta return one correctly-shaped but signature-tampered ACK before
# the valid signed ACK. Participant-authenticated evidence access starts the
# delivery-only recovery; no client process receives the operator token.
kill_nakama
printf 'tamper_completion_once\n' >"$control_file"
start_nakama
run_phase recover3

printf 'up\n' >"$control_file"
run_phase cardinality

echo "Paper Raid signed-control v2 pinned Compose: create/resume/replace/complete, four deterministic SIGKILL windows, exact replay/conflict, 3/4/5 keys, cursor, epoch fencing, signed ACK tamper rejection, roots: PASS"
