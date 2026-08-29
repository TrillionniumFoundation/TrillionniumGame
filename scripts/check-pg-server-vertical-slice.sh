#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

run_root=${TRNM_PG_SERVER_EVIDENCE_ROOT:-run/pg-server-vertical-slice}
image=${TRNM_POSTGRES_IMAGE:-postgres:17.6-alpine3.22@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94}
container=${TRNM_POSTGRES_CONTAINER:-trnm-pg-server-${GITHUB_RUN_ID:-local}-$$}
port=${TRNM_POSTGRES_PORT:-55432}
listen=${TRNM_SERVER_LISTEN:-127.0.0.1:17351}
database=trnm_server_slice
password=TrnmPgServerSlicePassword0123456789
token=0123456789abcdefghijklmnopqrstuvwxyz-._~
source_commit=$(git rev-parse HEAD)
source_tree=$(git rev-parse HEAD^{tree})

rm -rf "$run_root"
mkdir -p "$run_root"

cleanup() {
  if [[ -n "${server_pid:-}" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

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
  "$image" \
  >"$run_root/container-id.txt"

docker inspect "$container" >"$run_root/container-inspect.json"
docker image inspect "$image" >"$run_root/image-inspect.json"

for _ in $(seq 1 120); do
  if docker exec "$container" pg_isready -U postgres -d "$database" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
docker exec "$container" pg_isready -U postgres -d "$database" \
  | tee "$run_root/pg-isready.txt"

docker exec -i "$container" psql \
  -v ON_ERROR_STOP=1 \
  -U postgres \
  -d "$database" \
  < migrations/postgresql/0001_foundation_up.sql \
  >"$run_root/migration.log" 2>&1

cargo build \
  --workspace \
  --all-targets \
  --locked \
  >"$run_root/build.log" 2>&1

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
    "$binary" \
    >"$run_root/${label}.stdout" \
    2>"$run_root/${label}.stderr" &
  server_pid=$!
  for _ in $(seq 1 100); do
    if grep -q 'listening=' "$run_root/${label}.stdout"; then
      return 0
    fi
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

send_request() {
  label=$1
  command=$2
  revision=$3
  TRNM_SERVER_SLICE_LISTEN="$listen" \
  TRNM_SERVER_SLICE_TOKEN="$token" \
  TRNM_SERVER_SLICE_COMMAND="$command" \
  TRNM_SERVER_SLICE_REVISION="$revision" \
  TRNM_SERVER_SLICE_OUTPUT="$run_root/${label}.json" \
  python3 - <<'PY'
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

host, port_text = os.environ["TRNM_SERVER_SLICE_LISTEN"].rsplit(":", 1)
body = json.dumps(
    {
        "command": int(os.environ["TRNM_SERVER_SLICE_COMMAND"]),
        "expected_revision": int(os.environ["TRNM_SERVER_SLICE_REVISION"]),
    },
    separators=(",", ":"),
)
headers = [
    "POST /v2/rpc/trnm_pg_vertical_slice HTTP/1.1",
    "host: localhost",
    f"authorization: Bearer {os.environ['TRNM_SERVER_SLICE_TOKEN']}",
    f"content-length: {len(body.encode('utf-8'))}",
    "connection: close",
]
payload = ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")
with socket.create_connection((host, int(port_text)), timeout=3.0) as connection:
    connection.sendall(payload)
    connection.shutdown(socket.SHUT_WR)
    chunks = []
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
raw = b"".join(chunks).decode("utf-8")
head, response_body = raw.split("\r\n\r\n", 1)
status = int(head.splitlines()[0].split()[1])
value = {"status": status, "body": json.loads(response_body), "raw": raw}
Path(os.environ["TRNM_SERVER_SLICE_OUTPUT"]).write_text(
    json.dumps(value, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
}

start_server apply
send_request apply 1 0
wait "$server_pid"
unset server_pid
jq -e '
  .status == 200 and
  .body.duplicate == false and
  .body.revision == 1 and
  .body.event_count == 1 and
  .body.outbox_count == 1
' "$run_root/apply.json" >/dev/null

start_server restart-duplicate
send_request restart-duplicate 1 0
wait "$server_pid"
unset server_pid
jq -e '
  .status == 200 and
  .body.duplicate == true and
  .body.revision == 1 and
  .body.event_count == 1 and
  .body.outbox_count == 1
' "$run_root/restart-duplicate.json" >/dev/null

start_server stale-revision
send_request stale-revision 2 0
wait "$server_pid"
unset server_pid
jq -e '
  .status == 409 and
  .body.error == "aborted" and
  .body.reason == "entity_revision_mismatch"
' "$run_root/stale-revision.json" >/dev/null

query() {
  docker exec "$container" psql -At \
    -U postgres \
    -d "$database" \
    -c "$1"
}

{
  printf 'receipts=%s\n' "$(query 'SELECT count(*) FROM trnm_command_receipts;')"
  printf 'events=%s\n' "$(query 'SELECT count(*) FROM trnm_events;')"
  printf 'outbox=%s\n' "$(query 'SELECT count(*) FROM trnm_outbox;')"
  printf 'command_outbox=%s\n' "$(query 'SELECT count(*) FROM trnm_command_outbox;')"
  printf 'entity_revision=%s\n' "$(query 'SELECT revision FROM trnm_entity_heads;')"
} | tee "$run_root/database-counts.txt"

grep -qx 'receipts=1' "$run_root/database-counts.txt"
grep -qx 'events=1' "$run_root/database-counts.txt"
grep -qx 'outbox=1' "$run_root/database-counts.txt"
grep -qx 'command_outbox=1' "$run_root/database-counts.txt"
grep -qx 'entity_revision=1' "$run_root/database-counts.txt"

test ! -s "$run_root/apply.stderr"
test ! -s "$run_root/restart-duplicate.stderr"
test ! -s "$run_root/stale-revision.stderr"
grep -q 'drained=true' "$run_root/apply.stdout"
grep -q 'drained=true' "$run_root/restart-duplicate.stdout"
grep -q 'drained=true' "$run_root/stale-revision.stdout"

cat >"$run_root/result.json" <<JSON
{
  "schema": "trillionnium.pg-server-vertical-slice-result.v1",
  "repository": "TrillionniumFoundation/TrillionniumGame",
  "commit": "$source_commit",
  "tree": "$source_tree",
  "profile": "postgresql",
  "image": "$image",
  "assertions": {
    "first_command_applied": true,
    "restart_replayed_exact_duplicate": true,
    "stale_revision_rejected": true,
    "receipt_count": 1,
    "event_count": 1,
    "outbox_count": 1,
    "command_outbox_count": 1,
    "entity_revision": 1
  },
  "claims": {
    "live_postgresql_slice_passed": true,
    "cockroachdb_slice_passed": false,
    "nakama_wire_compatible": false,
    "sg4_complete": false,
    "production_ready": false
  }
}
JSON

find "$run_root" -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  >"$run_root/SHA256SUMS"

trap - EXIT
cleanup
