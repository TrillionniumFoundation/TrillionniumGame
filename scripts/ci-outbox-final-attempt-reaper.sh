#!/usr/bin/env bash
set -Eeuo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *) echo "usage: $0 postgresql|cockroachdb" >&2; exit 64 ;;
esac

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
commit=$(git rev-parse HEAD)
run_id=${TRNM_RUN_ID:-local}
evidence_root=${TRNM_EVIDENCE_ROOT:-run/outbox-final-attempt-reaper}
evidence="$evidence_root/$profile"
rm -rf "$evidence"
mkdir -p "$evidence/logs"
exec > >(tee "$evidence/logs/run.log") 2>&1

container="trnm-outbox-final-attempt-${profile}-${run_id//[^a-zA-Z0-9_.-]/-}"
database_url=
cleanup() {
  if [[ -n "${container:-}" ]]; then
    docker rm -f "$container" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'status=$?; printf "status=failed\nexit_code=%s\n" "$status" >"$evidence/result.env"; exit "$status"' ERR

image_for() {
  python3 - "$profile" <<'PY'
import json
import sys
from pathlib import Path
profile = sys.argv[1]
value = json.loads(Path('config/database-test-images.json').read_text(encoding='utf-8'))['profiles'][profile]['image']
if '@sha256:' not in value:
    raise SystemExit(f'image is not digest-pinned: {value}')
print(value)
PY
}

migration_for() {
  python3 - "$profile" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

profile = sys.argv[1]
lock_path = Path('migrations/MIGRATION_CHAIN.lock.json')
lock = json.loads(lock_path.read_text(encoding='utf-8'))
if lock.get('schema') != 'trillionnium.migration-chain-lock.v1':
    raise SystemExit('migration lock schema mismatch')
profiles = lock.get('profiles')
if not isinstance(profiles, dict) or profile not in profiles:
    raise SystemExit(f'migration lock profile missing: {profile}')
row = profiles[profile]
ordered = row.get('ordered_files')
if not isinstance(ordered, list) or len(ordered) != 1:
    raise SystemExit(
        f'final-attempt lane requires exactly one locked migration for {profile}'
    )
entry = ordered[0]
path_value = entry.get('path')
expected_blob = entry.get('git_blob_sha1')
if not isinstance(path_value, str) or not path_value:
    raise SystemExit(f'locked migration path is invalid for {profile}')
if not isinstance(expected_blob, str) or len(expected_blob) != 40:
    raise SystemExit(f'locked migration blob is invalid for {profile}')
path = Path(path_value)
if not path.is_file():
    raise SystemExit(f'locked migration file is absent: {path_value}')
expected_directory = row.get('directory')
if expected_directory != path.parent.as_posix():
    raise SystemExit(
        f'locked migration directory mismatch: {expected_directory!r} != {path.parent.as_posix()!r}'
    )
actual_blob = subprocess.check_output(
    ['git', 'hash-object', path_value], text=True
).strip()
if actual_blob != expected_blob:
    raise SystemExit(
        f'locked migration blob mismatch for {path_value}: '
        f'expected={expected_blob} actual={actual_blob}'
    )
print(path_value)
print(expected_blob)
PY
}

image=$(image_for)
mapfile -t migration_identity < <(migration_for)
test "${#migration_identity[@]}" -eq 2
migration=${migration_identity[0]}
migration_blob=${migration_identity[1]}
printf 'repository=TrillionniumFoundation/TrillionniumGame\ncommit=%s\nprofile=%s\nimage=%s\nrun_id=%s\nmigration=%s\nmigration_blob_sha1=%s\n' \
  "$commit" "$profile" "$image" "$run_id" "$migration" "$migration_blob" \
  >"$evidence/identity.env"
docker pull "$image" 2>&1 | tee "$evidence/logs/docker-pull.log"

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]]
}

if [[ "$profile" == postgresql ]]; then
  expected="${TRNM_POSTGRES_IMAGE:-$image}"
  [[ "$expected" == "$image" ]]
  docker run -d --name "$container" --network host \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=trnm "$image" -c fsync=on -c synchronous_commit=on \
    >"$evidence/container-id.txt"
  ready=false
  for _ in $(seq 1 120); do
    container_running || break
    if docker exec -e PGPASSWORD=postgres "$container" pg_isready \
      -h 127.0.0.1 -p 5432 -U postgres -d trnm >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "$ready" == true ]]
  database_url='postgresql://postgres:postgres@127.0.0.1:5432/trnm?sslmode=disable'
  docker exec -e PGPASSWORD=postgres -i "$container" psql \
    -h 127.0.0.1 -U postgres -d trnm -v ON_ERROR_STOP=1 <"$migration" \
    2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec -e PGPASSWORD=postgres "$container" psql -At \
      -h 127.0.0.1 -U postgres -d trnm -v ON_ERROR_STOP=1 -c "$1"
  }
else
  expected="${TRNM_COCKROACH_IMAGE:-$image}"
  [[ "$expected" == "$image" ]]
  docker run -d --name "$container" --network host "$image" start-single-node \
    --insecure --listen-addr=127.0.0.1:26257 --advertise-addr=127.0.0.1:26257 \
    --http-addr=127.0.0.1:8080 --store=type=mem,size=1GiB \
    --cache=128MiB --max-sql-memory=128MiB >"$evidence/container-id.txt"
  ready=false
  for _ in $(seq 1 180); do
    container_running || break
    if docker exec "$container" cockroach sql --insecure \
      --host=127.0.0.1:26257 --execute='SELECT 1' >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "$ready" == true ]]
  docker exec "$container" cockroach sql --insecure \
    --host=127.0.0.1:26257 --execute='CREATE DATABASE IF NOT EXISTS trnm'
  database_url='postgresql://root@127.0.0.1:26257/trnm?sslmode=disable'
  docker exec -i "$container" cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=trnm <"$migration" \
    2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec "$container" cockroach sql --insecure --format=tsv \
      --host=127.0.0.1:26257 --database=trnm --execute="$1" | tail -n +2
  }
fi

cargo build --locked --package trnm-persistence-pg \
  --bin trnm-pg-command --bin trnm-outbox-worker \
  2>&1 | tee "$evidence/logs/cargo-build.log"

export TRNM_DATABASE_URL="$database_url"
export TRNM_DATABASE_PROFILE="$profile"
export TRNM_SCHEMA_SOURCE_COMMIT="$commit"
export TRNM_SCHEMA_APPLIED_AT_MS=1
export TRNM_OUTBOX_DATABASE_URL="$database_url"
export TRNM_OUTBOX_DATABASE_PROFILE="$profile"
export TRNM_OUTBOX_DATABASE_TLS_MODE=plaintext-candidate
export TRNM_OUTBOX_ALLOW_PLAINTEXT_DATABASE=1
export TRNM_OUTBOX_NODE_ID_HEX=$(printf 'ff%.0s' {1..16})
export TRNM_OUTBOX_BATCH_SIZE=16
export TRNM_OUTBOX_LEASE_DURATION_MS=1000
export TRNM_OUTBOX_MAX_ATTEMPTS=1
export TRNM_OUTBOX_POLL_INTERVAL_MS=10
export TRNM_OUTBOX_MAX_BACKOFF_MS=1000
command_bin=target/debug/trnm-pg-command
worker_bin=target/debug/trnm-outbox-worker

run_scenario() {
  local boundary=$1 entity_byte=$2 command_byte=$3 fingerprint_byte=$4
  local bootstrap_state_byte=$5 next_state_byte=$6 committed_at_ms=$7
  local expected_exit=$8 expected_dead_letter_count=$9
  local scenario="$evidence/$boundary" spool="$evidence/$boundary/spool"
  local intent_byte=$((command_byte + 2)) intent_hex spool_path crash_status
  intent_hex=$(python3 - "$intent_byte" <<'PY'
import sys
print(f"{int(sys.argv[1]):02x}" * 16)
PY
)
  spool_path="$spool/$intent_hex.json"
  mkdir -p "$scenario" "$spool"
  "$command_bin" bootstrap \
    --entity-byte "$entity_byte" --authority-generation 1 \
    --state-byte "$bootstrap_state_byte" --updated-at-ms 10 >"$scenario/bootstrap.json"
  "$command_bin" apply \
    --entity-byte "$entity_byte" --command-byte "$command_byte" \
    --fingerprint-byte "$fingerprint_byte" --expected-revision 0 \
    --authority-generation 1 --state-byte "$next_state_byte" \
    --committed-at-ms "$committed_at_ms" >"$scenario/apply.json"
  export TRNM_OUTBOX_SPOOL_DIRECTORY="$spool" TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS=1
  unset TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY || true
  case "$boundary" in
    crash-before-publish) export TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY=1 ;;
    crash-after-publish) export TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY=1 ;;
    *) echo "unknown boundary $boundary" >&2; return 64 ;;
  esac
  # A command used as an `if` condition is exempt from `errexit` and the
  # inherited ERR trap. Capture the intentional failpoint status without
  # suppressing fail-fast behavior for any surrounding command.
  if "$worker_bin" run-once >"$scenario/worker-crash.stdout" 2>"$scenario/worker-crash.stderr"; then
    crash_status=0
  else
    crash_status=$?
  fi
  test "$crash_status" -eq "$expected_exit"
  case "$boundary" in
    crash-before-publish)
      grep -q 'before durable spool publication' "$scenario/worker-crash.stderr"
      test ! -e "$spool_path"
      ;;
    crash-after-publish)
      grep -q 'after durable spool and before database acknowledgement' "$scenario/worker-crash.stderr"
      test -f "$spool_path"
      sha256sum "$spool_path" >"$scenario/spool-before.sha256"
      ;;
  esac
  sleep 2
  unset TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY || true
  "$worker_bin" run-once >"$scenario/reaper.stdout" 2>"$scenario/reaper.stderr"
  grep -q "dead_lettered=${expected_dead_letter_count}" "$scenario/reaper.stdout"
  test "$(sql_exec "SELECT COUNT(*) FROM trnm_outbox WHERE intent_id = decode('$intent_hex','hex');")" = 0
  test "$(sql_exec "SELECT COUNT(*) FROM trnm_outbox_dead_letters WHERE intent_id = decode('$intent_hex','hex');")" = "$expected_dead_letter_count"
  case "$boundary" in
    crash-before-publish)
      test ! -e "$spool_path"
      printf 'possible_lost_effect_declared=true\nspool_effect_count=0\ndead_letter_count=%s\n' \
        "$expected_dead_letter_count" >"$scenario/result.env"
      ;;
    crash-after-publish)
      test -f "$spool_path"
      sha256sum -c "$scenario/spool-before.sha256"
      spool_count=$(find "$spool" -maxdepth 1 -type f -name '*.json' | wc -l)
      test "$spool_count" -eq 1
      printf 'possible_lost_effect_declared=false\nspool_effect_count=1\ndead_letter_count=%s\n' \
        "$expected_dead_letter_count" >"$scenario/result.env"
      ;;
  esac
}

run_scenario crash-before-publish 20 21 22 23 24 3100 71 1
run_scenario crash-after-publish 30 31 32 33 34 4100 70 1

printf 'status=passed\nprofile=%s\ncommit=%s\n' "$profile" "$commit" >"$evidence/result.env"
find "$evidence" -type f -print0 | sort -z | xargs -0 sha256sum >"$evidence/files.sha256"
cat "$evidence/result.env"
