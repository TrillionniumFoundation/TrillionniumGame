#!/usr/bin/env bash
set -euo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *) echo 'usage: ci-trnm-server-live.sh postgresql|cockroachdb' >&2; exit 64 ;;
esac

root=$(git rev-parse --show-toplevel)
cd "$root"

candidate_sha=${CANDIDATE_SHA:-$(git rev-parse HEAD)}
test "$(git rev-parse HEAD)" = "$candidate_sha"
test "${#candidate_sha}" -eq 40
test -z "${candidate_sha//[0-9a-f]/}"
candidate_tree=$(git rev-parse HEAD^{tree})
run_id=${TRNM_RUN_ID:-local-$profile}
evidence_root=${TRNM_EVIDENCE_ROOT:-run/server-live}
evidence="$evidence_root/$profile"
rm -rf "$evidence"
mkdir -p "$evidence"

server_port=${TRNM_SERVER_PORT:-17350}
admin_token='trnm_server_live_admin_token_0123456789abcdef'
container="trnm-server-live-${profile}-${run_id//[^A-Za-z0-9_.-]/-}"
server_pid=''

postgres_image='postgres:17.6-alpine3.22@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
cockroach_image='cockroachdb/cockroach:v24.1.2@sha256:105b9d1e10e4845c9c59266bef3c27ff8b82eeaeb1b464c75423408c3a2968ba'

cleanup() {
  status=$?
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

docker rm -f "$container" >/dev/null 2>&1 || true

if [[ "$profile" == postgresql ]]; then
  db_port=${TRNM_POSTGRES_PORT:-55435}
  database_url="postgresql://trnm:trnm_live_password@127.0.0.1:${db_port}/trnm"
  docker pull "$postgres_image" | tee "$evidence/image-pull.log"
  docker run --rm -d \
    --name "$container" \
    -e POSTGRES_DB=trnm \
    -e POSTGRES_USER=trnm \
    -e POSTGRES_PASSWORD=trnm_live_password \
    -p "127.0.0.1:${db_port}:5432" \
    "$postgres_image" > "$evidence/container-id.txt"
  for _ in $(seq 1 120); do
    if docker exec "$container" pg_isready -U trnm -d trnm >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
  docker exec "$container" pg_isready -U trnm -d trnm
  db_scalar() {
    docker exec "$container" psql -X -U trnm -d trnm -At -v ON_ERROR_STOP=1 -c "$1"
  }
else
  database_url='postgresql://root@127.0.0.1:26257/trnm?sslmode=disable'
  docker pull "$cockroach_image" | tee "$evidence/image-pull.log"
  docker run --rm -d \
    --name "$container" \
    --network host \
    "$cockroach_image" \
    start-single-node \
    --insecure \
    --listen-addr=127.0.0.1:26257 \
    --http-addr=127.0.0.1:18081 \
    --store=/cockroach/cockroach-data > "$evidence/container-id.txt"
  for _ in $(seq 1 160); do
    if docker exec "$container" /cockroach/cockroach sql \
      --insecure --host=127.0.0.1:26257 --execute='SELECT 1' >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
  docker exec "$container" /cockroach/cockroach sql \
    --insecure --host=127.0.0.1:26257 \
    --execute='CREATE DATABASE IF NOT EXISTS trnm'
  db_scalar() {
    docker exec "$container" /cockroach/cockroach sql \
      --insecure --host=127.0.0.1:26257 --database=trnm \
      --format=csv --execute="$1" | tail -n 1
  }
fi

docker inspect "$container" > "$evidence/container-inspect.json"
printf '%s\n' "$candidate_sha" > "$evidence/candidate-commit.txt"
printf '%s\n' "$candidate_tree" > "$evidence/candidate-tree.txt"
printf '%s\n' "$profile" > "$evidence/profile.txt"
printf '%s\n' "$run_id" > "$evidence/run-id.txt"
rustc --version --verbose > "$evidence/rustc-version.txt"
cargo --version --verbose > "$evidence/cargo-version.txt"
docker version > "$evidence/docker-version.txt"
python3 --version > "$evidence/python-version.txt" 2>&1

cargo build --locked -p trnm-persistence-pg --bin trnm-server \
  2>&1 | tee "$evidence/cargo-build.log"
binary=target/debug/trnm-server
test -x "$binary"
sha256sum "$binary" > "$evidence/server-binary-sha256.txt"

export TRNM_SERVER_BIND="127.0.0.1:${server_port}"
export TRNM_SERVER_DATABASE_URL="$database_url"
export TRNM_SERVER_DATABASE_PROFILE="$profile"
export TRNM_SERVER_SCHEMA_SOURCE_COMMIT="$candidate_sha"
export TRNM_SERVER_ADMIN_TOKEN="$admin_token"
export TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE=1
export TRNM_SERVER_MAX_REQUEST_BYTES=131072
export TRNM_SERVER_READ_TIMEOUT_MS=5000
export TRNM_SERVER_WRITE_TIMEOUT_MS=10000

"$binary" check-config > "$evidence/check-config.log" 2>&1
if grep -F 'trnm_live_password' "$evidence/check-config.log"; then
  echo 'database credential leaked by check-config' >&2
  exit 1
fi
if grep -F "$admin_token" "$evidence/check-config.log"; then
  echo 'admin token leaked by check-config' >&2
  exit 1
fi
"$binary" migrate > "$evidence/migrate.log" 2>&1
grep -F "migration profile=${profile} applied=true table_count=10" "$evidence/migrate.log"

start_server() {
  phase=$1
  "$binary" serve > "$evidence/server-${phase}.log" 2>&1 &
  server_pid=$!
  printf '%s\n' "$server_pid" > "$evidence/server-${phase}.pid"
}

wait_for_server_exit() {
  phase=$1
  for _ in $(seq 1 120); do
    if ! kill -0 "$server_pid" 2>/dev/null; then
      wait "$server_pid"
      server_pid=''
      return 0
    fi
    sleep 0.1
  done
  echo "server did not exit after ${phase} drain" >&2
  return 1
}

start_server primary
python3 scripts/trnm-server-live-client.py \
  --port "$server_port" \
  --token "$admin_token" \
  --phase primary \
  --output "$evidence/client-primary.json" \
  2>&1 | tee "$evidence/client-primary.log"
wait_for_server_exit primary

start_server restart
python3 scripts/trnm-server-live-client.py \
  --port "$server_port" \
  --token "$admin_token" \
  --phase restart \
  --output "$evidence/client-restart.json" \
  2>&1 | tee "$evidence/client-restart.log"
wait_for_server_exit restart

if grep -F 'trnm_live_password' "$evidence"/server-*.log; then
  echo 'database credential leaked by server log' >&2
  exit 1
fi
if grep -F "$admin_token" "$evidence"/server-*.log; then
  echo 'admin token leaked by server log' >&2
  exit 1
fi

entity_hex=$(printf '01%.0s' {1..16})
state=$(db_scalar "SELECT revision || '|' || last_event_sequence FROM trnm_entity_heads WHERE entity_id = decode('${entity_hex}', 'hex')")
test "$state" = '3|3'
printf 'entity_head=%s\n' "$state" > "$evidence/database-assertions.txt"

for table in trnm_entity_heads trnm_command_receipts trnm_events trnm_outbox trnm_command_outbox; do
  count=$(db_scalar "SELECT count(*) FROM ${table}")
  case "$table" in
    trnm_entity_heads) expected=1 ;;
    *) expected=3 ;;
  esac
  test "$count" = "$expected"
  printf '%s=%s\n' "$table" "$count" >> "$evidence/database-assertions.txt"
done
pending=$(db_scalar 'SELECT count(*) FROM trnm_outbox WHERE state = 0')
test "$pending" = '3'
printf 'pending_outbox=%s\n' "$pending" >> "$evidence/database-assertions.txt"
source_commit=$(db_scalar 'SELECT source_commit FROM trnm_schema_metadata WHERE singleton = 1')
test "$source_commit" = "$candidate_sha"
printf 'schema_source_commit=%s\n' "$source_commit" >> "$evidence/database-assertions.txt"

cat > "$evidence/summary.json" <<EOF
{"schema":"trillionnium.server-live-evidence.v1","repository":"TrillionniumFoundation/TrillionniumGame","commit":"${candidate_sha}","tree":"${candidate_tree}","profile":"${profile}","check_config":true,"fresh_migration":true,"health_ready":true,"unauthenticated_mutation_rejected":true,"http_bootstrap_commit_duplicate_conflict":true,"websocket_json_commit":true,"response_loss_exact_receipt_replay":true,"authenticated_drain":true,"process_restart_exact_receipt_replay":true,"entity_revision":3,"event_sequence":3,"command_receipts":3,"events":3,"outbox_intents":3,"production_pitr":false,"multi_node":false,"wire_compatible":false,"production_ready":false}
EOF
python3 -m json.tool "$evidence/summary.json" >/dev/null
find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
cat "$evidence/summary.json"
cat "$evidence/database-assertions.txt"
echo "trnm-server live contract passed: profile=${profile} evidence=${evidence}"
