#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
bash scripts/project-preflight.sh --dev

for command_name in node npm python3 rg timeout; do
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
  echo "ERROR: Docker and Compose access are required for the smoke gate" >&2
  exit 1
fi
"${docker_cmd[@]}" compose version >/dev/null

tmp=$(mktemp -d)
env_file="$tmp/compose.env"
state_file_k0="$tmp/blackbox-state-k0.json"
state_file_k1="$tmp/blackbox-state-k1.json"
negative_log="$tmp/missing-secrets.log"
client_dir="$tmp/client"
mkdir -p "$client_dir"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  if [[ -s "$env_file" ]]; then
    "${docker_cmd[@]}" compose --env-file "$env_file" -f "$root/compose.yaml" \
      down -v --remove-orphans --rmi local >/dev/null 2>&1
  fi
  rm -rf "$tmp"
  exit "$status"
}
trap cleanup EXIT INT TERM

# A missing secret must fail while rendering the Compose model, before a
# partially configured service or volume can be created.
if env -i PATH="$PATH" HOME="${HOME:-/tmp}" \
  "${docker_cmd[@]}" compose -f "$root/compose.yaml" config --quiet \
  >"$negative_log" 2>&1; then
  echo "ERROR: Compose accepted a configuration with all required secrets absent" >&2
  exit 1
fi
if ! rg 'is required' "$negative_log" >/dev/null; then
  echo "ERROR: missing-secret failure was not caused by a required variable" >&2
  sed -n '1,120p' "$negative_log" >&2
  exit 1
fi

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
node - "$env_file" "$http_port" "$source_revision" "$source_tree" "$source_date_epoch" "$sbom_sha256" <<'NODE'
import { generateKeyPairSync, randomBytes } from "node:crypto";
import { writeFileSync } from "node:fs";

const envFile = process.argv[2];
const httpPort = process.argv[3];
const sourceRevision = process.argv[4];
const sourceTree = process.argv[5];
const sourceDateEpoch = process.argv[6];
const sbomSha256 = process.argv[7];
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
const agentOne = keyPair();
const agentTwo = keyPair();
const suffix = `${process.pid}-${randomBytes(4).toString("hex")}`;
const random = () => randomBytes(32).toString("hex");
const issuerKeys = JSON.stringify({ "blackbox-hepta-v1": issuer.publicKey });
const controlIssuerKeys = JSON.stringify({ "blackbox-hepta-control-v2": controlIssuer.publicKey });
const authorityK0ID = "blackbox-nakama-k0";
const authorityK1ID = "blackbox-nakama-k1";

const lines = [
  `TRNM_NAKAMA_COMPOSE_PROJECT=trnm-nakama-p0-${suffix}`,
  `TRNM_NAKAMA_HTTP_PORT=${httpPort}`,
  `TRNM_NAKAMA_IMAGE=trillionnium-nakama:p0-${suffix}`,
  `TRNM_NAKAMA_SOURCE_REVISION=${sourceRevision}`,
  `TRNM_NAKAMA_SOURCE_TREE=${sourceTree}`,
  `TRNM_NAKAMA_SOURCE_DATE_EPOCH=${sourceDateEpoch}`,
  `TRNM_NAKAMA_SBOM_SHA256=${sbomSha256}`,
  `TRNM_NAKAMA_DB_PASSWORD=${random()}`,
  `NAKAMA_SERVER_KEY=${random()}`,
  `NAKAMA_SESSION_ENCRYPTION_KEY=${random()}`,
  `NAKAMA_SESSION_REFRESH_ENCRYPTION_KEY=${random()}`,
  `NAKAMA_RUNTIME_HTTP_KEY=${random()}`,
  `NAKAMA_CONSOLE_PASSWORD=${random()}`,
  `NAKAMA_CONSOLE_SIGNING_KEY=${random()}`,
  `TRNM_HEPTA_ISSUER_KEYS='${issuerKeys}'`,
  `TRNM_HEPTA_CONTROL_ISSUER_KEYS='${controlIssuerKeys}'`,
  "TRNM_HEPTA_BASE_URL=http://127.0.0.1:1",
  `TRNM_HEPTA_SERVICE_TOKEN=${random()}`,
  "TRNM_HEPTA_ISSUER_KEY_ID=blackbox-hepta-v1",
  `TRNM_HEPTA_ISSUER_PRIVATE_SEED=${issuer.privateSeed}`,
  `TRNM_NAKAMA_AUTHORITY_KEY_ID=${authorityK0ID}`,
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY=${authorityK0.privateSeed}`,
  `TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS='${JSON.stringify({ [authorityK0ID]: authorityK0.privateSeed })}'`,
  `TRNM_BLACKBOX_AUTHORITY_K0_KEY_ID=${authorityK0ID}`,
  `TRNM_BLACKBOX_AUTHORITY_K0_PRIVATE_SEED=${authorityK0.privateSeed}`,
  `TRNM_BLACKBOX_AUTHORITY_K0_PUBLIC_KEY=${authorityK0.publicKey}`,
  `TRNM_BLACKBOX_AUTHORITY_K1_KEY_ID=${authorityK1ID}`,
  `TRNM_BLACKBOX_AUTHORITY_K1_PRIVATE_SEED=${authorityK1.privateSeed}`,
  `TRNM_BLACKBOX_AUTHORITY_K1_PUBLIC_KEY=${authorityK1.publicKey}`,
  `TRNM_NAKAMA_OPERATOR_TOKEN=${random()}`,
  `TRNM_AGENT_ONE_PRIVATE_SEED=${agentOne.privateSeed}`,
  `TRNM_AGENT_ONE_PUBLIC_KEY=${agentOne.publicKey}`,
  `TRNM_AGENT_TWO_PRIVATE_SEED=${agentTwo.privateSeed}`,
  `TRNM_AGENT_TWO_PUBLIC_KEY=${agentTwo.publicKey}`,
  `TRNM_BLACKBOX_CUSTOM_ID_ONE=trnm-blackbox-a-${suffix}`,
  `TRNM_BLACKBOX_CUSTOM_ID_TWO=trnm-blackbox-b-${suffix}`,
  `TRNM_BLACKBOX_LOGICAL_MATCH_ID_K0=blackbox-match-k0-${suffix}`,
  `TRNM_BLACKBOX_LOGICAL_MATCH_ID_K1=blackbox-match-k1-${suffix}`,
  "TRNM_NAKAMA_MATCH_TICK_RATE=5",
  "",
];
writeFileSync(envFile, lines.join("\n"), { mode: 0o600 });
NODE
chmod 600 "$env_file"
recorded_http_port=$(sed -n 's/^TRNM_NAKAMA_HTTP_PORT=//p' "$env_file")
if [[ "$recorded_http_port" != "$http_port" ]]; then
  echo "ERROR: generated Compose env did not preserve the selected HTTP port" >&2
  exit 1
fi

# Explicit export wins over any inherited/implementation-specific Compose
# interpolation behavior; the value is non-secret and is also recorded in the
# generated env file used for every subsequent Compose command.
export TRNM_NAKAMA_HTTP_PORT="$http_port"
if [[ "${docker_cmd[0]}" == "sudo" ]]; then
  # sudo's default env_reset intentionally drops the export. Reintroduce only
  # this non-secret port selector on the privileged side; all secrets continue
  # to come from the mode-0600 --env-file.
  compose=(sudo -n env "TRNM_NAKAMA_HTTP_PORT=$http_port" docker compose --env-file "$env_file" -f "$root/compose.yaml")
else
  compose=("${docker_cmd[@]}" compose --env-file "$env_file" -f "$root/compose.yaml")
fi

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
  if (!value) throw new Error(`missing rotation fixture ${name}`);
  return value.startsWith("'") && value.endsWith("'") ? value.slice(1, -1) : value;
};
const k0 = {
  id: raw("TRNM_BLACKBOX_AUTHORITY_K0_KEY_ID"),
  seed: raw("TRNM_BLACKBOX_AUTHORITY_K0_PRIVATE_SEED"),
};
const k1 = {
  id: raw("TRNM_BLACKBOX_AUTHORITY_K1_KEY_ID"),
  seed: raw("TRNM_BLACKBOX_AUTHORITY_K1_PRIVATE_SEED"),
};
let ring;
if (phase === "overlap-active-k1") {
  ring = { [k0.id]: k0.seed, [k1.id]: k1.seed };
} else if (phase === "k1-only") {
  ring = { [k1.id]: k1.seed };
} else {
  throw new Error(`unsupported authority phase ${phase}`);
}
const updates = new Map([
  ["TRNM_NAKAMA_AUTHORITY_KEY_ID", k1.id],
  ["TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY", k1.seed],
  ["TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS", `'${JSON.stringify(ring)}'`],
]);
const seen = new Set();
const rewritten = lines.map((line) => {
  const separator = line.indexOf("=");
  const name = separator > 0 ? line.slice(0, separator) : "";
  if (!updates.has(name)) return line;
  seen.add(name);
  return `${name}=${updates.get(name)}`;
});
if (seen.size !== updates.size) throw new Error("authority phase could not update every runtime variable");
writeFileSync(envFile, rewritten.join("\n") + "\n", { mode: 0o600 });
chmodSync(envFile, 0o600);
NODE
}

rendered_compose="$tmp/compose.json"
"${compose[@]}" config --format json >"$rendered_compose"
rendered_http_port=$(python3 - "$rendered_compose" <<'PY'
import json
import sys

model = json.load(open(sys.argv[1], encoding="utf-8"))
environment = model["services"]["nakama"].get("environment", {})
active_id = environment.get("TRNM_NAKAMA_AUTHORITY_KEY_ID")
active_private = environment.get("TRNM_NAKAMA_AUTHORITY_PRIVATE_KEY")
private_ring_raw = environment.get("TRNM_NAKAMA_AUTHORITY_PRIVATE_KEYS")
if not active_id or not active_private or not private_ring_raw:
    raise SystemExit("canonical Compose did not inject the complete Nakama authority ring")
private_ring = json.loads(private_ring_raw)
if private_ring.get(active_id) != active_private:
    raise SystemExit("rendered Nakama authority ring does not byte-match its active singleton")
ports = model["services"]["nakama"].get("ports", [])
if len(ports) != 1:
    raise SystemExit("expected exactly one rendered Nakama port")
port = ports[0]
if port.get("host_ip") != "127.0.0.1" or port.get("target") != 7350:
    raise SystemExit(f"unexpected rendered Nakama port: {port}")
print(port.get("published", ""))
PY
)
if [[ "$rendered_http_port" != "$http_port" ]]; then
  echo "ERROR: Compose rendered HTTP port $rendered_http_port instead of $http_port" >&2
  exit 1
fi
if ! "${compose[@]}" up -d --build --wait --wait-timeout 300; then
  echo "ERROR: Compose stack failed to start" >&2
  "${compose[@]}" logs --no-color --tail=200 >&2 || true
  exit 1
fi

postgres_id=$("${compose[@]}" ps -q postgres)
nakama_id=$("${compose[@]}" ps -q nakama)
endpoint=$("${compose[@]}" port nakama 7350)
if [[ "$endpoint" == ":0" || "$endpoint" == "127.0.0.1:0" ]]; then
  # Docker Engine 29 reports :0 and a null NetworkSettings entry for a port on
  # an internal Compose network even though HostConfig retains the effective
  # loopback publication. Query that binding directly.
  binding_json=$("${docker_cmd[@]}" inspect -f '{{json (index .HostConfig.PortBindings "7350/tcp")}}' "$nakama_id")
  endpoint=$(python3 - "$binding_json" <<'PY'
import json
import sys

bindings = json.loads(sys.argv[1])
if not isinstance(bindings, list) or len(bindings) != 1:
    raise SystemExit(f"expected exactly one effective 7350/tcp binding, got {bindings!r}")
print(f"{bindings[0]['HostIp']}:{bindings[0]['HostPort']}")
PY
  )
fi
if [[ ! "$endpoint" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
  echo "ERROR: Nakama was not published on one dynamic IPv4 loopback port: $endpoint" >&2
  exit 1
fi
nakama_port=${endpoint##*:}
if [[ "$nakama_port" == "0" ]]; then
  echo "ERROR: Docker did not assign an effective host port for Nakama" >&2
  exit 1
fi

project=$(sed -n 's/^TRNM_NAKAMA_COMPOSE_PROJECT=//p' "$env_file")
"${docker_cmd[@]}" inspect "$postgres_id" "$nakama_id" >"$tmp/containers.json"
"${docker_cmd[@]}" network inspect "${project}_backend" >"$tmp/network.json"
python3 - "$tmp/containers.json" "$tmp/network.json" <<'PY'
import json
import sys

containers = json.load(open(sys.argv[1], encoding="utf-8"))
network = json.load(open(sys.argv[2], encoding="utf-8"))[0]
if not network.get("Internal"):
    raise SystemExit("backend network is not internal")
if len(containers) != 2:
    raise SystemExit("expected exactly two inspected services")
for container in containers:
    name = container["Name"].lstrip("/")
    host = container["HostConfig"]
    if not host.get("ReadonlyRootfs"):
        raise SystemExit(f"{name}: root filesystem is writable")
    if "ALL" not in (host.get("CapDrop") or []):
        raise SystemExit(f"{name}: capabilities are not dropped")
    if "no-new-privileges:true" not in (host.get("SecurityOpt") or []):
        raise SystemExit(f"{name}: no-new-privileges is absent")

nakama = next(item for item in containers if "nakama" in item["Name"] and "postgres" not in item["Name"])
if nakama["Config"].get("User") in ("", "0", "root", "0:0"):
    raise SystemExit("nakama runs as root")
bindings = nakama["HostConfig"].get("PortBindings") or {}
if set(bindings) != {"7350/tcp"}:
    raise SystemExit(f"unexpected Nakama port bindings: {sorted(bindings)}")
for binding in bindings["7350/tcp"]:
    if binding.get("HostIp") != "127.0.0.1":
        raise SystemExit("Nakama port is not loopback-only")
nakama_networks = set(nakama["NetworkSettings"].get("Networks") or {})
if nakama_networks != {f"{network['Name'].removesuffix('_backend')}_backend", f"{network['Name'].removesuffix('_backend')}_edge"}:
    raise SystemExit(f"unexpected Nakama networks: {sorted(nakama_networks)}")

postgres = next(item for item in containers if "postgres" in item["Name"])
if postgres["HostConfig"].get("PortBindings"):
    raise SystemExit("PostgreSQL must not publish a host port")
postgres_networks = set(postgres["NetworkSettings"].get("Networks") or {})
if postgres_networks != {network["Name"]}:
    raise SystemExit(f"PostgreSQL escaped the internal backend: {sorted(postgres_networks)}")
print("compose isolation and hardening: ok")
PY

cp scripts/blackbox/package.json scripts/blackbox/package-lock.json scripts/blackbox/smoke.mjs "$client_dir/"
npm ci --prefix "$client_dir" --ignore-scripts --no-audit --no-fund >/dev/null

run_blackbox() {
  local phase=$1
  local expected=${2:-true}
  local state_path=${3:-$state_file_k0}
  local authority_slot=${4:-k0}
  local logical_slot=${5:-$authority_slot}
  (
    # The file is generated above from random bytes and is never supplied by a
    # caller. Source it as shell variables, then export only the credentials
    # required by this phase. In particular, the third-party JS client never
    # receives database, session, console, or Nakama authority private keys.
    # shellcheck disable=SC1090
    source "$env_file"

    # Remove every inherited export before constructing the phase-specific
    # allowlist. Values remain shell-local, so secrets are not placed in an
    # `env VAR=value` command line while the process is being started.
    while IFS= read -r variable_name; do
      export -n "${variable_name?}"
    done < <(compgen -e)

    NAKAMA_HOST=127.0.0.1
    NAKAMA_PORT="$nakama_port"
    EXPECT_READY="$expected"
    BLACKBOX_PHASE="$phase"
    TRNM_BLACKBOX_STATE_FILE="$state_path"
    case "$authority_slot" in
      k0)
        TRNM_BLACKBOX_EXPECTED_AUTHORITY_KEY_ID="$TRNM_BLACKBOX_AUTHORITY_K0_KEY_ID"
        TRNM_BLACKBOX_EXPECTED_AUTHORITY_PUBLIC_KEY="$TRNM_BLACKBOX_AUTHORITY_K0_PUBLIC_KEY"
        ;;
      k1)
        TRNM_BLACKBOX_EXPECTED_AUTHORITY_KEY_ID="$TRNM_BLACKBOX_AUTHORITY_K1_KEY_ID"
        TRNM_BLACKBOX_EXPECTED_AUTHORITY_PUBLIC_KEY="$TRNM_BLACKBOX_AUTHORITY_K1_PUBLIC_KEY"
        ;;
      *)
        echo "ERROR: unknown authority fixture slot: $authority_slot" >&2
        exit 64
        ;;
    esac
    case "$logical_slot" in
      k0) TRNM_BLACKBOX_LOGICAL_MATCH_ID="$TRNM_BLACKBOX_LOGICAL_MATCH_ID_K0" ;;
      k1) TRNM_BLACKBOX_LOGICAL_MATCH_ID="$TRNM_BLACKBOX_LOGICAL_MATCH_ID_K1" ;;
      *)
        echo "ERROR: unknown logical-match fixture slot: $logical_slot" >&2
        exit 64
        ;;
    esac
    export PATH HOME NAKAMA_HOST NAKAMA_PORT EXPECT_READY BLACKBOX_PHASE
    export NAKAMA_SERVER_KEY NAKAMA_RUNTIME_HTTP_KEY

    case "$phase" in
      health)
        ;;
      prepare)
        export TRNM_BLACKBOX_STATE_FILE TRNM_NAKAMA_OPERATOR_TOKEN
        export TRNM_HEPTA_ISSUER_KEY_ID TRNM_HEPTA_ISSUER_PRIVATE_SEED
        export TRNM_AGENT_ONE_PRIVATE_SEED TRNM_AGENT_ONE_PUBLIC_KEY
        export TRNM_AGENT_TWO_PRIVATE_SEED TRNM_AGENT_TWO_PUBLIC_KEY
        export TRNM_BLACKBOX_CUSTOM_ID_ONE TRNM_BLACKBOX_CUSTOM_ID_TWO
        export TRNM_BLACKBOX_LOGICAL_MATCH_ID
        ;;
      resume)
        export TRNM_BLACKBOX_STATE_FILE TRNM_NAKAMA_OPERATOR_TOKEN
        export TRNM_AGENT_TWO_PRIVATE_SEED
        export TRNM_BLACKBOX_EXPECTED_AUTHORITY_KEY_ID TRNM_BLACKBOX_EXPECTED_AUTHORITY_PUBLIC_KEY
        ;;
      retired-key-rejected)
        export TRNM_BLACKBOX_STATE_FILE TRNM_NAKAMA_OPERATOR_TOKEN
        ;;
      *)
        echo "ERROR: unknown black-box phase: $phase" >&2
        exit 64
        ;;
    esac

    timeout 45s node "$client_dir/smoke.mjs"
  )
}

wait_for_runtime_ready() {
  local label=$1
  local ready_output=""
  for _ in $(seq 1 45); do
    if ready_output=$(run_blackbox health true 2>&1); then
      break
    fi
    sleep 1
  done
  if [[ -z "$ready_output" ]] || ! printf '%s\n' "$ready_output" | rg '"ready":true' >/dev/null; then
    echo "ERROR: $label did not become ready" >&2
    printf '%s\n' "$ready_output" >&2
    "${compose[@]}" logs --no-color --tail=200 >&2 || true
    exit 1
  fi
  printf '%s\n' "$ready_output"
}

audit_state_file() {
  local candidate=$1
  if [[ ! -f "$candidate" || "$(stat -c '%a' "$candidate")" != "600" ]]; then
    echo "ERROR: black-box resume state is absent or not mode 0600: $candidate" >&2
    exit 1
  fi
  python3 - "$candidate" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
forbidden = ("private", "seed", "token", "password", "session")

def inspect(value, path="state"):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            if any(word in lowered for word in forbidden):
                raise SystemExit(f"resume state contains forbidden secret field: {path}.{key}")
            inspect(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            inspect(item, f"{path}[{index}]")

inspect(state)
print("black-box resume state secret audit: ok")
PY
}

wait_for_runtime_ready "initial K0 runtime"

# Exercise the actual v3.40.0 HTTP and realtime interfaces. The Node client
# independently implements the language-neutral Ed25519 framing and carries no
# Go runtime code.
prepare_output=$(run_blackbox prepare true "$state_file_k0" k0 k0 2>&1) || {
  echo "ERROR: authoritative prepare-phase black box failed" >&2
  printf '%s\n' "$prepare_output" >&2
  "${compose[@]}" logs --no-color --tail=200 >&2 || true
  exit 1
}
printf '%s\n' "$prepare_output"
if ! printf '%s\n' "$prepare_output" | rg '"phase":"prepare".*"exact_replay":true.*"tamper_rejected":true.*"out_of_order_rejected":true.*"broadcast_scopes_verified":true' >/dev/null; then
  echo "ERROR: prepare phase did not report all authoritative assertions" >&2
  exit 1
fi
audit_state_file "$state_file_k0"

# Hard-kill only Nakama. PostgreSQL and its named volume must survive so the
# next process can prove durable logical-match recovery and idempotency. Before
# restart, add K1, make it active, and retain K0 for historical snapshots.
postgres_before=$postgres_id
"${docker_cmd[@]}" kill --signal KILL "$nakama_id" >/dev/null
if [[ "$("${docker_cmd[@]}" inspect -f '{{.State.ExitCode}}' "$nakama_id")" != "137" ]]; then
  echo "ERROR: Nakama did not record the expected SIGKILL exit code" >&2
  exit 1
fi
if [[ "$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$postgres_before")" != "true" ]]; then
  echo "ERROR: PostgreSQL stopped during the Nakama crash test" >&2
  exit 1
fi
set_authority_phase overlap-active-k1
"${compose[@]}" up -d --no-deps nakama >/dev/null
nakama_id=$("${compose[@]}" ps -q nakama)
if [[ "$("${compose[@]}" ps -q postgres)" != "$postgres_before" ]]; then
  echo "ERROR: PostgreSQL was recreated during the Nakama-only restart" >&2
  exit 1
fi
wait_for_runtime_ready "K0/K1 overlap runtime with active K1"

historical_resume_output=$(run_blackbox resume true "$state_file_k0" k0 k0 2>&1) || {
  echo "ERROR: K0 historical crash-resume failed during K0/K1 overlap" >&2
  printf '%s\n' "$historical_resume_output" >&2
  "${compose[@]}" logs --no-color --tail=240 >&2 || true
  exit 1
}
printf '%s\n' "$historical_resume_output"
if ! printf '%s\n' "$historical_resume_output" | rg '"phase":"resume".*"post_crash_replay_exact":true.*"event_count":5.*"evidence_byte_identical":true.*"conflicting_completion_rejected":true.*"completed_runtime_terminated":true.*"broadcast_scopes_verified":true.*"authority_signature_verified":true.*"authority_key_id":"blackbox-nakama-k0"' >/dev/null; then
  echo "ERROR: historical K0 resume did not retain and verify its original authority" >&2
  exit 1
fi

# A session created after the active switch must be signed by K1. Persist it,
# kill Nakama again, then remove K0 before the next process starts.
rotated_prepare_output=$(run_blackbox prepare true "$state_file_k1" k1 k1 2>&1) || {
  echo "ERROR: active-K1 authoritative prepare phase failed" >&2
  printf '%s\n' "$rotated_prepare_output" >&2
  "${compose[@]}" logs --no-color --tail=240 >&2 || true
  exit 1
}
printf '%s\n' "$rotated_prepare_output"
if ! printf '%s\n' "$rotated_prepare_output" | rg '"phase":"prepare".*"exact_replay":true.*"tamper_rejected":true.*"out_of_order_rejected":true.*"broadcast_scopes_verified":true' >/dev/null; then
  echo "ERROR: active-K1 prepare phase did not report all authoritative assertions" >&2
  exit 1
fi
audit_state_file "$state_file_k1"

postgres_before=$postgres_id
"${docker_cmd[@]}" kill --signal KILL "$nakama_id" >/dev/null
if [[ "$("${docker_cmd[@]}" inspect -f '{{.State.ExitCode}}' "$nakama_id")" != "137" ]]; then
  echo "ERROR: Nakama did not record SIGKILL before K0 retirement" >&2
  exit 1
fi
if [[ "$("${docker_cmd[@]}" inspect -f '{{.State.Running}}' "$postgres_before")" != "true" ]]; then
  echo "ERROR: PostgreSQL stopped before the K0 retirement test" >&2
  exit 1
fi
set_authority_phase k1-only
"${compose[@]}" up -d --no-deps nakama >/dev/null
nakama_id=$("${compose[@]}" ps -q nakama)
if [[ "$("${compose[@]}" ps -q postgres)" != "$postgres_before" ]]; then
  echo "ERROR: PostgreSQL was recreated during the K0 retirement restart" >&2
  exit 1
fi
wait_for_runtime_ready "K1-only runtime"

retired_output=$(run_blackbox retired-key-rejected true "$state_file_k0" k0 k0 2>&1) || {
  echo "ERROR: removed-K0 fail-closed black box failed" >&2
  printf '%s\n' "$retired_output" >&2
  "${compose[@]}" logs --no-color --tail=240 >&2 || true
  exit 1
}
printf '%s\n' "$retired_output"
if ! printf '%s\n' "$retired_output" | rg '"phase":"retired-key-rejected".*"removed_authority_failed_closed":true' >/dev/null; then
  echo "ERROR: K0 snapshot was not proven fail-closed after K0 removal" >&2
  exit 1
fi

active_resume_output=$(run_blackbox resume true "$state_file_k1" k1 k1 2>&1) || {
  echo "ERROR: active K1 crash-resume black box failed after K0 removal" >&2
  printf '%s\n' "$active_resume_output" >&2
  "${compose[@]}" logs --no-color --tail=240 >&2 || true
  exit 1
}
printf '%s\n' "$active_resume_output"
if ! printf '%s\n' "$active_resume_output" | rg '"phase":"resume".*"post_crash_replay_exact":true.*"event_count":5.*"evidence_byte_identical":true.*"conflicting_completion_rejected":true.*"completed_runtime_terminated":true.*"broadcast_scopes_verified":true.*"authority_signature_verified":true.*"authority_key_id":"blackbox-nakama-k1"' >/dev/null; then
  echo "ERROR: active K1 resume did not retain and verify K1 after K0 removal" >&2
  exit 1
fi

# Readiness must fail while the process health endpoint remains available after
# loss of its database/storage dependency.
"${compose[@]}" stop -t 10 postgres >/dev/null
unready_output=""
for _ in $(seq 1 30); do
  if unready_output=$(run_blackbox health false 2>&1); then
    printf '%s\n' "$unready_output"
    break
  fi
  sleep 1
done
if [[ -z "$unready_output" ]] || ! printf '%s\n' "$unready_output" | rg '"ready":false' >/dev/null; then
  echo "ERROR: runtime did not fail readiness after PostgreSQL stopped" >&2
  printf '%s\n' "$unready_output" >&2
  "${compose[@]}" logs --no-color --tail=160 >&2 || true
  exit 1
fi
if [[ "$("${docker_cmd[@]}" inspect -f '{{.State.Health.Status}}' "$nakama_id")" != "healthy" ]]; then
  echo "ERROR: Nakama liveness failed together with database readiness" >&2
  exit 1
fi

echo "Nakama Compose smoke: K0 -> {K0,K1}/active K1 -> K1-only fail-closed rotation: PASS"
