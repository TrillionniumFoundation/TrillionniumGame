#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

run_root=${TRNM_PG_RESPONSE_LOSS_ROOT:-run/pg-server-response-loss}
image=${TRNM_POSTGRES_IMAGE:-postgres:17.6-alpine3.22@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94}
container=${TRNM_POSTGRES_CONTAINER:-trnm-pg-response-loss-${GITHUB_RUN_ID:-local}-$$}
port=${TRNM_POSTGRES_PORT:-55433}
listen=${TRNM_SERVER_LISTEN:-127.0.0.1:17352}
database=trnm_response_loss
password=TrnmPgResponseLossPassword0123456789
token=0123456789abcdefghijklmnopqrstuvwxyz-._~
source_commit=$(git rev-parse HEAD)
source_tree=$(git rev-parse HEAD^{tree})

rm -rf "$run_root"
mkdir -p "$run_root"

capture_postgres_diagnostics() {
  docker inspect "$container" >"$run_root/container-inspect.json" 2>&1 || true
  docker logs "$container" >"$run_root/postgres.log" 2>&1 || true
}

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    capture_postgres_diagnostics
    [[ -f "$run_root/migration.log" ]] && cat "$run_root/migration.log" >&2 || true
    [[ -f "$run_root/postgres.log" ]] && tail -n 200 "$run_root/postgres.log" >&2 || true
  fi
  if [[ -n "${server_pid:-}" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

docker run --rm -d \
  --name "$container" \
  -e POSTGRES_DB="$database" \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD="$password" \
  -p "127.0.0.1:${port}:5432" \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --cap-add CHOWN \
  --cap-add DAC_OVERRIDE \
  --cap-add FOWNER \
  --cap-add SETGID \
  --cap-add SETUID \
  "$image" >"$run_root/container-id.txt"

docker image inspect "$image" >"$run_root/image-inspect.json"

# The Docker image exposes a temporary postmaster during initialization.
# Require the final PID-1 postgres process and two consecutive SQL probes so a
# successful readiness probe cannot be followed by the init-time shutdown.
stable_probes=0
: >"$run_root/pg-probe.log"
for _ in $(seq 1 200); do
  if docker exec "$container" sh -ec 'test "$(cat /proc/1/comm)" = postgres' \
      >/dev/null 2>&1 \
    && docker exec "$container" psql -X -U postgres -d "$database" -Atqc 'SELECT 1' \
      >"$run_root/pg-probe.txt" 2>>"$run_root/pg-probe.log" \
    && grep -qx '1' "$run_root/pg-probe.txt"; then
    stable_probes=$((stable_probes + 1))
    if [[ $stable_probes -ge 2 ]]; then
      break
    fi
  else
    stable_probes=0
  fi
  sleep 0.25
done
if [[ $stable_probes -lt 2 ]]; then
  echo 'final PostgreSQL postmaster did not become stably queryable' >&2
  exit 1
fi
docker exec "$container" pg_isready -U postgres -d "$database" \
  >"$run_root/pg-isready.txt"
if ! docker exec -i "$container" psql \
  -X \
  -v ON_ERROR_STOP=1 \
  -U postgres \
  -d "$database" \
  < migrations/postgresql/0001_foundation_up.sql \
  >"$run_root/migration.log" 2>&1; then
  echo 'authoritative PostgreSQL migration failed' >&2
  exit 1
fi

cargo build --workspace --all-targets --locked >"$run_root/build.log" 2>&1
binary=target/debug/examples/trnm_server_pg_slice
test -x "$binary"
database_url="postgresql://postgres:${password}@127.0.0.1:${port}/${database}"

start_server() {
  label=$1
  : >"$run_root/${label}.stdout"
  : >"$run_root/${label}.stderr"
  TRNM_DATABASE_URL="$database_url" \
  TRNM_DATABASE_PROFILE=postgresql \
  TRNM_SCHEMA_SOURCE_COMMIT="$source_commit" \
  TRNM_SERVER_LISTEN="$listen" \
  TRNM_SERVER_DEV_TOKEN="$token" \
  TRNM_SERVER_MAX_REQUESTS=1 \
    "$binary" >"$run_root/${label}.stdout" 2>"$run_root/${label}.stderr" &
  server_pid=$!
  for _ in $(seq 1 100); do
    grep -q 'listening=' "$run_root/${label}.stdout" && return 0
    if ! kill -0 "$server_pid" 2>/dev/null; then
      cat "$run_root/${label}.stdout" >&2 || true
      cat "$run_root/${label}.stderr" >&2 || true
      return 1
    fi
    sleep 0.05
  done
  echo "server did not become ready" >&2
  return 1
}

start_server lost-response
TRNM_SERVER_SLICE_LISTEN="$listen" \
TRNM_SERVER_SLICE_TOKEN="$token" \
python3 - <<'PY'
import os, socket, struct
host, port_text = os.environ['TRNM_SERVER_SLICE_LISTEN'].rsplit(':', 1)
body = '{"command":9,"expected_revision":0}'
request = (
    'POST /v2/rpc/trnm_pg_vertical_slice HTTP/1.1\r\n'
    'host: localhost\r\n'
    f"authorization: Bearer {os.environ['TRNM_SERVER_SLICE_TOKEN']}\r\n"
    f'content-length: {len(body)}\r\n'
    'connection: close\r\n\r\n'
    + body
).encode()
connection = socket.create_connection((host, int(port_text)), timeout=3.0)
connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
connection.sendall(request)
connection.close()
PY

# The response write may fail because of the injected reset. The durable commit
# is the authority, so wait for the receipt rather than requiring process exit 0.
set +e
wait "$server_pid"
lost_process_rc=$?
set -e
unset server_pid
printf '%s\n' "$lost_process_rc" >"$run_root/lost-process-rc.txt"

query() {
  docker exec "$container" psql -X -At -U postgres -d "$database" -c "$1"
}
for _ in $(seq 1 100); do
  [[ $(query 'SELECT count(*) FROM trnm_command_receipts;') == 1 ]] && break
  sleep 0.05
done
test "$(query 'SELECT count(*) FROM trnm_command_receipts;')" = 1
test "$(query 'SELECT count(*) FROM trnm_events;')" = 1
test "$(query 'SELECT count(*) FROM trnm_outbox;')" = 1

start_server replay
TRNM_SERVER_SLICE_LISTEN="$listen" \
TRNM_SERVER_SLICE_TOKEN="$token" \
TRNM_SERVER_SLICE_OUTPUT="$run_root/replay.json" \
python3 - <<'PY'
from __future__ import annotations
import json, os, socket
from pathlib import Path
host, port_text = os.environ['TRNM_SERVER_SLICE_LISTEN'].rsplit(':', 1)
body = '{"command":9,"expected_revision":0}'
request = (
    'POST /v2/rpc/trnm_pg_vertical_slice HTTP/1.1\r\n'
    'host: localhost\r\n'
    f"authorization: Bearer {os.environ['TRNM_SERVER_SLICE_TOKEN']}\r\n"
    f'content-length: {len(body)}\r\n'
    'connection: close\r\n\r\n'
    + body
).encode()
with socket.create_connection((host, int(port_text)), timeout=3.0) as connection:
    connection.sendall(request)
    connection.shutdown(socket.SHUT_WR)
    raw = b''
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            break
        raw += chunk
text = raw.decode()
head, response_body = text.split('\r\n\r\n', 1)
value = {'status': int(head.splitlines()[0].split()[1]), 'body': json.loads(response_body)}
Path(os.environ['TRNM_SERVER_SLICE_OUTPUT']).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
PY
wait "$server_pid"
unset server_pid
jq -e '.status == 200 and .body.duplicate == true and .body.revision == 1 and .body.event_count == 1 and .body.outbox_count == 1' \
  "$run_root/replay.json" >/dev/null

test "$(query 'SELECT count(*) FROM trnm_command_receipts;')" = 1
test "$(query 'SELECT count(*) FROM trnm_events;')" = 1
test "$(query 'SELECT count(*) FROM trnm_outbox;')" = 1
test "$(query 'SELECT count(*) FROM trnm_command_outbox;')" = 1
test "$(query 'SELECT revision FROM trnm_entity_heads;')" = 1

cat >"$run_root/result.json" <<JSON
{
  "schema": "trillionnium.pg-response-loss-result.v1",
  "repository": "TrillionniumFoundation/TrillionniumGame",
  "commit": "$source_commit",
  "tree": "$source_tree",
  "profile": "postgresql",
  "image": "$image",
  "fault": "client TCP reset after complete request before response read",
  "assertions": {
    "durable_receipt_after_response_loss": true,
    "durable_event_after_response_loss": true,
    "durable_outbox_after_response_loss": true,
    "restart_replayed_exact_duplicate": true,
    "duplicate_visible_effects": 0,
    "acknowledged_or_committed_command_loss": 0
  },
  "claims": {
    "postgresql_ambiguous_response_slice_passed": true,
    "socket_protocol_compatible": false,
    "cockroachdb_passed": false,
    "sg4_complete": false,
    "production_ready": false
  }
}
JSON

capture_postgres_diagnostics
find "$run_root" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum >"$run_root/SHA256SUMS"

trap - EXIT INT TERM
cleanup
