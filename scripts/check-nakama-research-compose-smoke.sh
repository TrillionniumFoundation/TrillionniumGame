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
source_date_epoch=$(git show -s --format=%ct HEAD)
sbom_sha256=$(sha256sum runtime/sbom.cdx.json | cut -d' ' -f1)
node - "$env_file" "$http_port" "$state_dir" "$source_revision" "$source_tree" "$source_date_epoch" "$sbom_sha256" <<'NODE'
import { generateKeyPairSync, randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

const [envFile, httpPort, stateDir, sourceRevision, sourceTree, sourceDateEpoch, sbomSha256] = process.argv.slice(2);
const keyPair = () => {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  return {
    publicKey: publicKey.export({ type: "spki", format: "der" }).subarray(-32).toString("base64"),
    privateSeed: privateKey.export({ type: "pkcs8", format: "der" }).subarray(-32).toString("base64"),
  };
};
const issuer = keyPair();
const controlIssuer = keyPair();
const authorityK0 = keyPair();
const authorityK1 = keyPair();
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
  `TRNM_NAKAMA_SOURCE_DATE_EPOCH=${sourceDateEpoch}`,
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
  "TRNM_NAKAMA_AUTHORITY_KEY_ID=paper-raid-nakama-k0",
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY=${authorityK0.privateSeed}`,
  `TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS='${JSON.stringify({ "paper-raid-nakama-k0": authorityK0.publicKey })}'`,
  `TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY=${authorityK0.publicKey}`,
  "TRNM_RESEARCH_AUTHORITY_K0_KEY_ID=paper-raid-nakama-k0",
  `TRNM_RESEARCH_AUTHORITY_K0_PUBLIC_KEY=${authorityK0.publicKey}`,
  "TRNM_RESEARCH_AUTHORITY_K1_KEY_ID=paper-raid-nakama-k1",
  `TRNM_RESEARCH_AUTHORITY_K1_PRIVATE_SEED=${authorityK1.privateSeed}`,
  `TRNM_RESEARCH_AUTHORITY_K1_PUBLIC_KEY=${authorityK1.publicKey}`,
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
bash scripts/lint-nakama-compose-env.sh "$env_file" >/dev/null
obsolete_env_file="$tmp/obsolete-private-ring.env"
cp "$env_file" "$obsolete_env_file"
printf '%s\n' 'TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS={}' >>"$obsolete_env_file"
if obsolete_lint_output=$(bash scripts/lint-nakama-compose-env.sh "$obsolete_env_file" 2>&1); then
  echo "ERROR: Compose env lint accepted the obsolete authority private-key ring" >&2
  exit 1
fi
if [[ "$obsolete_lint_output" != *"obsolete TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS is forbidden"* ]]; then
  echo "ERROR: Compose env lint did not return the explicit obsolete-ring failure" >&2
  exit 1
fi
rm -f "$obsolete_env_file"
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
    while IFS= read -r variable_name; do export -n "${variable_name?}"; done < <(compgen -e)
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
        if [[ "$phase" == "cardinality" ]]; then
          TRNM_NAKAMA_AUTHORITY_PUBLIC_KEY="$TRNM_RESEARCH_AUTHORITY_K1_PUBLIC_KEY"
        fi
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
      replay-k0-control-under-k1|retired-k0-rejected)
        export TRNM_BLACKBOX_STATE_FILE
        ;;
      *)
        echo "ERROR: unknown Paper Raid black-box phase: $phase" >&2
        exit 64
        ;;
    esac
    timeout 90s node "$client_dir/research-session-smoke.mjs"
  )
}

set_authority_phase() {
  local phase=$1
  node - "$env_file" "$phase" <<'NODE'
import { chmodSync, readFileSync, writeFileSync } from "node:fs";

const [envFile, phase] = process.argv.slice(2);
const lines = readFileSync(envFile, "utf8").trimEnd().split("\n");
const values = new Map();
for (const line of lines) {
  const separator = line.indexOf("=");
  if (separator > 0) values.set(line.slice(0, separator), line.slice(separator + 1));
}
const raw = (name) => {
  const value = values.get(name);
  if (!value) throw new Error(`missing authority rotation fixture ${name}`);
  return value.startsWith("'") && value.endsWith("'") ? value.slice(1, -1) : value;
};
const k0ID = raw("TRNM_RESEARCH_AUTHORITY_K0_KEY_ID");
const k0Public = raw("TRNM_RESEARCH_AUTHORITY_K0_PUBLIC_KEY");
const k1ID = raw("TRNM_RESEARCH_AUTHORITY_K1_KEY_ID");
const k1Public = raw("TRNM_RESEARCH_AUTHORITY_K1_PUBLIC_KEY");
const activeID = raw("TRNM_NAKAMA_AUTHORITY_KEY_ID");
const k1Private = values.has("TRNM_RESEARCH_AUTHORITY_K1_PRIVATE_SEED")
  ? raw("TRNM_RESEARCH_AUTHORITY_K1_PRIVATE_SEED")
  : activeID === k1ID ? raw("TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY") : "";
if (!k1Private) throw new Error("active K1 singleton private key is unavailable");
let publicRegistry;
if (phase === "overlap-active-k1") {
  publicRegistry = { [k0ID]: k0Public, [k1ID]: k1Public };
} else if (phase === "k1-only") {
  publicRegistry = { [k1ID]: k1Public };
} else {
  throw new Error(`unsupported authority phase ${phase}`);
}
const updates = new Map([
  ["TRNM_NAKAMA_AUTHORITY_KEY_ID", k1ID],
  ["TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY", k1Private],
  ["TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS", `'${JSON.stringify(publicRegistry)}'`],
]);
const rewritten = [];
const seen = new Set();
for (const line of lines) {
  const separator = line.indexOf("=");
  const name = separator > 0 ? line.slice(0, separator) : "";
  if (name === "TRNM_RESEARCH_AUTHORITY_K1_PRIVATE_SEED") continue;
  if (updates.has(name)) {
    rewritten.push(`${name}=${updates.get(name)}`);
    seen.add(name);
  } else {
    rewritten.push(line);
  }
}
if (seen.size !== updates.size) throw new Error("authority rotation did not update every active signer variable");
writeFileSync(envFile, rewritten.join("\n") + "\n", { mode: 0o600 });
chmodSync(envFile, 0o600);
NODE
  bash scripts/lint-nakama-compose-env.sh "$env_file" >/dev/null
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

capture_research_rotation_rows() {
  local logical_session_id=$1
  local control_command_id=$2
  local output=$3
  local raw="$tmp/research-rotation-rows.raw"
  if [[ ! "$logical_session_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]] ||
    [[ ! "$control_command_id" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
    echo "ERROR: invalid generated research storage identity" >&2
    exit 64
  fi
  "${compose[@]}" exec -T postgres \
    psql -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -U postgres -d trillionnium_nakama \
    -v "logical_session_id=$logical_session_id" -v "control_command_id=$control_command_id" >"$raw" <<'SQL'
SELECT collection, key, version, encode(convert_to(value::text, 'UTF8'), 'hex')
FROM storage
WHERE (collection = 'trnm_research_session_v1' AND key = :'logical_session_id')
   OR (collection = 'trnm_research_control_v2' AND key = :'control_command_id')
ORDER BY collection COLLATE "C", key COLLATE "C";
SQL
  python3 - "$raw" "$output" "$logical_session_id" "$control_command_id" <<'PY'
import base64
import hashlib
import json
import pathlib
import sys

raw_path, output_path, logical_session_id, control_command_id = sys.argv[1:]
lines = pathlib.Path(raw_path).read_text(encoding="utf-8").splitlines()
if len(lines) != 2:
    raise SystemExit(f"expected one research session and one control row, got {len(lines)}")
rows = {}
proof_rows = []
for line in lines:
    fields = line.split("\t")
    if len(fields) != 4 or not all(fields):
        raise SystemExit("research rotation row omitted collection, key, version, or value")
    value_bytes = bytes.fromhex(fields[3])
    value_text = value_bytes.decode("utf-8")
    value = json.loads(value_text)
    rows[fields[0]] = value
    proof_rows.append({
        "collection": fields[0],
        "key": fields[1],
        "version": fields[2],
        "value": value_text,
        "value_sha256": hashlib.sha256(value_bytes).hexdigest(),
    })

control = rows.get("trnm_research_control_v2")
session = rows.get("trnm_research_session_v1")
if not isinstance(control, dict) or not isinstance(session, dict):
    raise SystemExit("research rotation rows use unexpected collections")
if control.get("schema") != "trnm.nakama.stored-research-control-command.v3" or \
        control.get("command_id") != control_command_id or control.get("status") != "applied":
    raise SystemExit("captured control is not the expected applied v3 command")
if control.get("expected_response_authority_key_id") != "paper-raid-nakama-k0" or \
        control.get("response_authority_key_id") != "paper-raid-nakama-k0":
    raise SystemExit("captured control response is not bound to K0")
response = base64.b64decode(control["response_body_base64"], validate=True)
if hashlib.sha256(response).hexdigest() != control.get("response_sha256"):
    raise SystemExit("captured control response hash differs")
signature = base64.b64decode(control["response_signature_base64"], validate=True)
if len(signature) != 64:
    raise SystemExit("captured control response signature length differs")
if session.get("schema") != "trnm.nakama.stored-research-session.v1" or \
        session.get("logical_session_id") != logical_session_id:
    raise SystemExit("captured research session identity differs")
snapshot = base64.b64decode(session["core_snapshot_base64"], validate=True)
if hashlib.sha256(snapshot).hexdigest() != session.get("snapshot_sha256"):
    raise SystemExit("captured research snapshot hash differs")
outbox = session.get("completion_outbox")
if not isinstance(outbox, dict) or outbox.get("authority_key_id") != "paper-raid-nakama-k0":
    raise SystemExit("captured completion outbox is absent or not bound to K0")
outbox_body = base64.b64decode(outbox["request_body_base64"], validate=True)
if hashlib.sha256(outbox_body).hexdigest() != outbox.get("request_sha256"):
    raise SystemExit("captured completion outbox request hash differs")

proof = {
    "schema": "trnm.nakama.research-k0-rotation-storage-proof.v1",
    "logical_session_id": logical_session_id,
    "control_command_id": control_command_id,
    "control_response_sha256": hashlib.sha256(response).hexdigest(),
    "control_response_signature_sha256": hashlib.sha256(signature).hexdigest(),
    "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
    "completion_outbox_sha256": hashlib.sha256(
        json.dumps(outbox, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
    "completion_outbox_request_sha256": hashlib.sha256(outbox_body).hexdigest(),
    "rows": proof_rows,
}
pathlib.Path(output_path).write_text(
    json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
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
pending_k0_preflight="$tmp/pending-k0-private-retirement.json"
pending_preflight_status=0
if pending_preflight_output=$(bash scripts/check-nakama-authority-private-retirement.sh \
  --env-file "$env_file" \
  --retiring-key-id paper-raid-nakama-k0 \
  --evidence-file "$pending_k0_preflight" \
  --writers-fenced \
  --compose-file "$root/compose.yaml" \
  --compose-file "$root/compose.research-smoke.yaml" 2>&1); then
  echo "ERROR: private-key retirement preflight accepted a pending K0 control" >&2
  exit 1
else
  pending_preflight_status=$?
fi
if [[ "$pending_preflight_status" != 3 ]]; then
  echo "ERROR: pending K0 retirement preflight returned $pending_preflight_status instead of blocker status 3" >&2
  printf '%s\n' "$pending_preflight_output" >&2
  exit 1
fi
python3 - "$pending_k0_preflight" <<'PY'
import json
import sys

evidence = json.load(open(sys.argv[1], encoding="utf-8"))
if evidence.get("safe_to_destroy_private_key") is not False:
    raise SystemExit("pending K0 preflight did not fail closed")
if not any(item.get("reason") == "pending_retiring_authority" for item in evidence.get("blockers", [])):
    raise SystemExit("pending K0 preflight did not identify the retiring authority")
print("research rotation preflight: pending K0 correctly blocks private-key destruction")
PY
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

# Completion and its v3 control response are now durable under K0 while Hepta
# is down. Fence every writer, capture the exact session/control/outbox rows,
# and require the offline read-only preflight to prove that no pending K0
# command remains before the env file loses its only K0 private seed.
"${compose[@]}" stop -t 20 nakama >/dev/null
readarray -t rotation_ids < <(python3 - "$state_file" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
print(state["complete_request"]["logical_session_id"])
print(state["complete_request"]["control"]["claim"]["command_id"])
PY
)
if (( ${#rotation_ids[@]} != 2 )); then
  echo "ERROR: research rotation fixture omitted the session or control identity" >&2
  exit 1
fi
rotation_session_id=${rotation_ids[0]}
rotation_control_id=${rotation_ids[1]}
k0_before_rotation="$tmp/k0-before-private-retirement.json"
k0_overlap_after_replay="$tmp/k0-overlap-after-control-replay.json"
capture_research_rotation_rows "$rotation_session_id" "$rotation_control_id" "$k0_before_rotation"
python3 - "$state_file" "$k0_before_rotation" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
proof = json.load(open(sys.argv[2], encoding="utf-8"))
if state.get("complete_response_sha256") != proof.get("control_response_sha256"):
    raise SystemExit("RPC response bytes differ from the captured applied v3 control response")
print("research rotation evidence: RPC response, applied control, snapshot, and outbox hashes pinned")
PY

safe_k0_preflight="$tmp/safe-k0-private-retirement.json"
bash scripts/check-nakama-authority-private-retirement.sh \
  --env-file "$env_file" \
  --retiring-key-id paper-raid-nakama-k0 \
  --evidence-file "$safe_k0_preflight" \
  --writers-fenced \
  --compose-file "$root/compose.yaml" \
  --compose-file "$root/compose.research-smoke.yaml"
python3 - "$safe_k0_preflight" <<'PY'
import json
import sys

evidence = json.load(open(sys.argv[1], encoding="utf-8"))
if evidence.get("safe_to_destroy_private_key") is not True or evidence.get("blocker_count") != 0:
    raise SystemExit("drained K0 private-key retirement did not prove zero blockers")
print("research rotation preflight: all K0 controls drained before private-key destruction")
PY

k0_private_sha256=$(python3 - "$env_file" <<'PY'
import hashlib
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY="):
        print(hashlib.sha256(line.rstrip("\n").split("=", 1)[1].encode("utf-8")).hexdigest())
        break
else:
    raise SystemExit("active K0 private seed is absent before rotation")
PY
)
set_authority_phase overlap-active-k1
python3 - "$env_file" "$k0_private_sha256" <<'PY'
import hashlib
import sys

env_file, retired_digest = sys.argv[1:]
for line in open(env_file, encoding="utf-8"):
    if "=" not in line:
        continue
    name, value = line.rstrip("\n").split("=", 1)
    if "PRIVATE" in name and hashlib.sha256(value.encode("utf-8")).hexdigest() == retired_digest:
        raise SystemExit(f"retired K0 private seed remains in {name}")
print("research rotation env: K0 private material destroyed after zero-pending preflight")
PY
start_nakama
nakama_id=$("${compose[@]}" ps -q nakama)
"${docker_cmd[@]}" inspect "$nakama_id" >"$tmp/rotated-nakama.json"
python3 - "$tmp/rotated-nakama.json" <<'PY'
import json
import sys

container = json.load(open(sys.argv[1], encoding="utf-8"))[0]
environment = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
if environment.get("TRNM_NAKAMA_AUTHORITY_KEY_ID") != "paper-raid-nakama-k1":
    raise SystemExit("rotated research runtime did not make K1 active")
if "TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS" in environment:
    raise SystemExit("rotated research runtime retained the forbidden private-key ring")
registry = json.loads(environment.get("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS", "{}"))
if set(registry) != {"paper-raid-nakama-k0", "paper-raid-nakama-k1"}:
    raise SystemExit("rotated research runtime did not retain exactly K0+K1 public verification keys")
if not environment.get("TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY"):
    raise SystemExit("rotated research runtime has no active singleton private key")
print("research authority rotation: K0 public retained, K1 singleton private active")
PY

# An applied v3 response is an immutable K0 artifact. Replay its original
# signed request under the K1-active process without any issuer or authority
# private fixture, require exact response bytes, and pin both database rows.
replay_output=$(run_phase replay-k0-control-under-k1 2>&1) || {
  echo "ERROR: K1-active runtime could not replay the K0-applied v3 control" >&2
  printf '%s\n' "$replay_output" >&2
  exit 1
}
printf '%s\n' "$replay_output"
if [[ "$replay_output" != *'"exact_k0_applied_control_replay":true'* ]] ||
  [[ "$replay_output" != *'"completion_authority_key_id":"paper-raid-nakama-k0"'* ]]; then
  echo "ERROR: K0 applied-control overlap proof is incomplete" >&2
  exit 1
fi
capture_research_rotation_rows "$rotation_session_id" "$rotation_control_id" "$k0_overlap_after_replay"
if ! cmp -s "$k0_before_rotation" "$k0_overlap_after_replay"; then
  echo "ERROR: K1-active exact control replay changed K0 session/control/outbox bytes or versions" >&2
  exit 1
fi

# Now make Hepta return one correctly-shaped but signature-tampered ACK before
# the valid signed ACK. Participant-authenticated evidence starts delivery-only
# recovery; no client process receives the operator token.
printf 'tamper_completion_once\n' >"$control_file"
run_phase recover3

printf 'up\n' >"$control_file"

# K0 public retirement is separate from private retirement: after delivery is
# final, pin the updated outbox row, stop the writer, remove K0 public, and prove
# archive(snapshot), evidence(completion), and applied-control replay all fail
# without changing either row. A fresh K1-only 4/5-member run follows.
"${compose[@]}" stop -t 20 nakama >/dev/null
k0_public_before="$tmp/k0-before-public-retirement.json"
k0_public_after="$tmp/k0-after-public-retirement-failures.json"
capture_research_rotation_rows "$rotation_session_id" "$rotation_control_id" "$k0_public_before"
set_authority_phase k1-only
start_nakama
nakama_id=$("${compose[@]}" ps -q nakama)
"${docker_cmd[@]}" inspect "$nakama_id" >"$tmp/k1-only-nakama.json"
python3 - "$tmp/k1-only-nakama.json" <<'PY'
import json
import sys

container = json.load(open(sys.argv[1], encoding="utf-8"))[0]
environment = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
if environment.get("TRNM_NAKAMA_AUTHORITY_KEY_ID") != "paper-raid-nakama-k1":
    raise SystemExit("K1-only research runtime did not retain K1 as active")
registry = json.loads(environment.get("TRNM_NAKAMA_AUTHORITY_PUBLIC_KEYS", "{}"))
if set(registry) != {"paper-raid-nakama-k1"}:
    raise SystemExit("K1-only research runtime still exposes K0 public material")
if "TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS" in environment:
    raise SystemExit("K1-only research runtime retained the forbidden private-key ring")
print("research public retirement: K1-only registry active")
PY
retired_output=$(run_phase retired-k0-rejected 2>&1) || {
  echo "ERROR: retired K0 research artifacts did not fail closed" >&2
  printf '%s\n' "$retired_output" >&2
  exit 1
}
printf '%s\n' "$retired_output"
if [[ "$retired_output" != *'"removed_k0_public_failed_closed":true'* ]] ||
  [[ "$retired_output" != *'"rejected_paths":["snapshot_archive","completion_evidence","applied_control_replay"]'* ]] ||
  [[ "$retired_output" != *'"explicit_missing_key_errors":true'* ]]; then
  echo "ERROR: retired K0 research failure proof is incomplete" >&2
  exit 1
fi
capture_research_rotation_rows "$rotation_session_id" "$rotation_control_id" "$k0_public_after"
if ! cmp -s "$k0_public_before" "$k0_public_after"; then
  echo "ERROR: missing-K0 research failures changed session/control/outbox bytes or versions" >&2
  exit 1
fi

run_phase cardinality

echo "Paper Raid signed-control v2 pinned Compose: four SIGKILL windows, pending-K0 retirement blocker then zero-pending drain, K1-active exact K0 v3 replay, K0-public removal fail-closed with byte-stable storage/outbox, independent K1 proof, 3/4/5 keys, cursor, ACK tamper rejection, roots: PASS"
