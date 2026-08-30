#!/usr/bin/env bash
set -Eeuo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *)
    echo 'usage: ci-outbox-final-attempt-reaper.sh postgresql|cockroachdb' >&2
    exit 64
    ;;
esac

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

for command in docker python3 cargo git sha256sum; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required" >&2
    exit 69
  }
done

case "$profile" in
  postgresql)
    image=${TRNM_POSTGRES_IMAGE:?TRNM_POSTGRES_IMAGE with immutable OCI digest is required}
    migration=migrations/postgresql/0001_foundation_up.sql
    ;;
  cockroachdb)
    image=${TRNM_COCKROACH_IMAGE:?TRNM_COCKROACH_IMAGE with immutable OCI digest is required}
    migration=migrations/cockroachdb/0001_foundation_up.sql
    ;;
esac
case "$image" in
  *@sha256:[0-9a-f][0-9a-f]*) ;;
  *)
    echo "$profile image must include @sha256:<digest>" >&2
    exit 64
    ;;
esac

test -f "$migration"
commit=$(git rev-parse HEAD)
tree=$(git rev-parse HEAD^{tree})
migration_sha=$(sha256sum "$migration" | awk '{print $1}')
run_id=${TRNM_RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)-$$}
evidence_root=${TRNM_EVIDENCE_ROOT:-run/outbox-final-attempt-reaper}
evidence="$evidence_root/$profile/$run_id"
spool="$evidence/spool"
mkdir -p "$evidence/logs" "$spool"
container="trnm-outbox-reaper-${profile}-${run_id//[^a-zA-Z0-9_.-]/-}"
password='trnm-local-evidence-password-0123456789'

container_exists() {
  docker inspect "$container" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]]
}

capture_container_diagnostics() {
  if container_exists; then
    docker inspect "$container" >"$evidence/logs/container-inspect.json" 2>&1 || true
    docker logs "$container" >"$evidence/logs/container.log" 2>&1 || true
  fi
}

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}

on_error() {
  status=$?
  trap - ERR
  set +e
  capture_container_diagnostics
  if [[ -s "$evidence/logs/container.log" ]]; then
    echo "--- $profile container log tail ---" >&2
    tail -n 200 "$evidence/logs/container.log" >&2
  fi
  exit "$status"
}

trap on_error ERR
trap cleanup EXIT INT TERM

printf '%s\n' \
  "repository=TrillionniumFoundation/TrillionniumGame" \
  "commit=$commit" \
  "tree=$tree" \
  "profile=$profile" \
  "image=$image" \
  "migration=$migration" \
  "migration_sha256=$migration_sha" \
  "run_id=$run_id" \
  >"$evidence/source.txt"

docker pull "$image" 2>&1 | tee "$evidence/logs/image-pull.log"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
image_repo_digests=$(docker image inspect --format '{{json .RepoDigests}}' "$image")
printf 'image_id=%s\nrepo_digests=%s\n' "$image_id" "$image_repo_digests" \
  >"$evidence/image.txt"

if [[ "$profile" == postgresql ]]; then
  docker run -d --name "$container" \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD="$password" \
    -e POSTGRES_DB=trnm \
    -p 127.0.0.1::5432 \
    "$image" >"$evidence/container-id.txt"

  ready=false
  for _ in $(seq 1 120); do
    container_running || break
    ready_count=$(docker logs "$container" 2>&1 \
      | grep -c 'database system is ready to accept connections' || true)
    if (( ready_count >= 2 )) \
      && docker exec -e PGPASSWORD="$password" "$container" \
        psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm \
        -c 'SELECT 1' 2>/dev/null | grep -qx '1'; then
      ready=true
      break
    fi
    sleep 1
  done
  [[ "$ready" == true ]]

  port=$(docker port "$container" 5432/tcp | awk -F: 'NR==1 {print $NF}')
  [[ "$port" =~ ^[0-9]+$ ]]
  database_url="postgresql://postgres:${password}@127.0.0.1:${port}/trnm"
  docker exec -i -e PGPASSWORD="$password" "$container" \
    psql -X -v ON_ERROR_STOP=1 -U postgres -d trnm \
    <"$migration" 2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec -e PGPASSWORD="$password" "$container" \
      psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm -c "$1"
  }
else
  docker run -d --name "$container" \
    --network host \
    "$image" start-single-node \
      --insecure \
      --listen-addr=127.0.0.1:26257 \
      --advertise-addr=127.0.0.1:26257 \
      --http-addr=127.0.0.1:8080 \
      --store=type=mem,size=1GiB \
      --cache=128MiB \
      --max-sql-memory=128MiB \
    >"$evidence/container-id.txt"

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
    --host=127.0.0.1:26257 --database=trnm \
    <"$migration" 2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec "$container" cockroach sql --insecure --format=tsv \
      --host=127.0.0.1:26257 --database=trnm --execute="$1" \
      | tail -n +2
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
export TRNM_OUTBOX_SPOOL_DIRECTORY="$spool"
export TRNM_OUTBOX_BATCH_SIZE=16
export TRNM_OUTBOX_LEASE_DURATION_MS=1000
export TRNM_OUTBOX_MAX_ATTEMPTS=1
export TRNM_OUTBOX_POLL_INTERVAL_MS=10
export TRNM_OUTBOX_MAX_BACKOFF_MS=1000

command_bin=target/debug/trnm-pg-command
worker_bin=target/debug/trnm-outbox-worker

"$command_bin" bootstrap \
  --entity-byte 144 --authority-generation 1 --state-byte 145 --updated-at-ms 10 \
  >"$evidence/bootstrap.json"
"$command_bin" apply \
  --entity-byte 144 --command-byte 160 --fingerprint-byte 161 \
  --expected-revision 0 --authority-generation 1 --state-byte 162 --committed-at-ms 20 \
  >"$evidence/apply.json"

export TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS=1
export TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY=1
if "$worker_bin" run-once \
  >"$evidence/worker-crash.stdout" \
  2>"$evidence/worker-crash.stderr"; then
  echo 'expected final-attempt post-delivery process exit did not occur' >&2
  exit 1
else
  crash_status=$?
fi
test "$crash_status" -eq 70
grep -q 'exiting after durable spool and before database acknowledgement' \
  "$evidence/worker-crash.stderr"
unset TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS
unset TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY

test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=1 AND attempt=1 AND lease_generation=1 AND owner_node IS NOT NULL" | tr -d '[:space:]')" = 1
spool_path="$spool/$(printf 'a2%.0s' {1..16}).json"
test -f "$spool_path"
sha256sum "$spool_path" >"$evidence/spool-before.sha256"

sleep 2
"$worker_bin" run-once | tee "$evidence/worker-reap.txt"
grep -q 'claimed=0 completed=0 retried=0 dead_lettered=0' \
  "$evidence/worker-reap.txt"

test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=3 AND attempt=1 AND lease_generation=1 AND owner_node IS NULL AND receipt_digest IS NULL AND dead_reason_digest IS NOT NULL" | tr -d '[:space:]')" = 1
test "$(sql_exec 'SELECT count(*) FROM trnm_outbox' | tr -d '[:space:]')" = 1
sha256sum "$spool_path" >"$evidence/spool-after.sha256"
test "$(cut -d' ' -f1 "$evidence/spool-before.sha256")" = \
  "$(cut -d' ' -f1 "$evidence/spool-after.sha256")"
test "$(find "$spool" -maxdepth 1 -type f -name "$(printf 'a2%.0s' {1..16}).json" | wc -l | tr -d '[:space:]')" = 1

capture_container_diagnostics

python3 - "$evidence" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
source = {}
for line in (root / "source.txt").read_text().splitlines():
    key, value = line.split("=", 1)
    source[key] = value
artifacts = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.name in {"manifest.json", "SHA256SUMS"}:
        continue
    data = path.read_bytes()
    artifacts.append({
        "path": str(path.relative_to(root)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    })
manifest = {
    "schema": "trillionnium.outbox-final-attempt-reaper-evidence.v1",
    "target_repository": source["repository"],
    "target_commit": source["commit"],
    "target_tree": source["tree"],
    "profile": source["profile"],
    "image": source["image"],
    "migration": source["migration"],
    "migration_sha256": source["migration_sha256"],
    "run_id": source["run_id"],
    "assertions": {
        "final_attempt_claimed": True,
        "process_exit_after_durable_spool": True,
        "lease_expired": True,
        "exhausted_lease_reaped_to_dead_letter": True,
        "owner_cleared": True,
        "receipt_absent": True,
        "dead_reason_present": True,
        "spool_bytes_unchanged": True,
        "duplicate_visible_effect_count": 0,
    },
    "artifacts": artifacts,
    "claims": {
        "single_host_process_failure_proven": True,
        "postgresql_and_cockroachdb_required": True,
        "multi_node_failover_proven": False,
        "endurance_proven": False,
        "compatibility_credit": False,
        "production_ready": False,
    },
    "limitations": [
        "This proves a bounded single-host process-failure case, not cross-host HA.",
        "The spool is not yet a concrete realtime, search, notification or provider consumer.",
        "Independent data-integrity review remains required before closing the outbox gap.",
    ],
}
(root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY

find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum >"$evidence/SHA256SUMS"
