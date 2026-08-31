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
mkdir -p "$evidence/logs"
container="trnm-outbox-reaper-${profile}-${run_id//[^a-zA-Z0-9_.-]/-}"
password='trnm-local-evidence-password-0123456789'

container_exists() { docker inspect "$container" >/dev/null 2>&1; }
container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]]
}
capture_container_diagnostics() {
  if container_exists; then
    docker inspect "$container" >"$evidence/logs/container-inspect.json" 2>&1 || true
    docker logs "$container" >"$evidence/logs/container.log" 2>&1 || true
  fi
}
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
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
printf 'image_id=%s\nrepo_digests=%s\n' "$image_id" "$image_repo_digests" >"$evidence/image.txt"

if [[ "$profile" == postgresql ]]; then
  docker run -d --name "$container" \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$password" -e POSTGRES_DB=trnm \
    -p 127.0.0.1::5432 "$image" >"$evidence/container-id.txt"
  ready=false
  for _ in $(seq 1 120); do
    container_running || break
    ready_count=$(docker logs "$container" 2>&1 | grep -c 'database system is ready to accept connections' || true)
    if (( ready_count >= 2 )) && docker exec -e PGPASSWORD="$password" "$container" \
      psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm -c 'SELECT 1' 2>/dev/null | grep -qx '1'; then
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
    psql -X -v ON_ERROR_STOP=1 -U postgres -d trnm <"$migration" \
    2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec -e PGPASSWORD="$password" "$container" \
      psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm -c "$1"
  }
else
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
  set +e
  "$worker_bin" run-once >"$scenario/worker-crash.stdout" 2>"$scenario/worker-crash.stderr"
  crash_status=$?
  set -e
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
  unset TRNM_OUTBOX_ENABLE_TEST_FAILPOINTS TRNM_OUTBOX_TEST_FAIL_BEFORE_DELIVERY \
    TRNM_OUTBOX_TEST_FAIL_AFTER_DELIVERY || true
  test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=1 AND attempt=1 AND lease_generation=1 AND owner_node IS NOT NULL" | tr -d '[:space:]')" = 1
  sleep 2
  "$worker_bin" run-once | tee "$scenario/worker-reap.txt"
  grep -q 'claimed=0 completed=0 retried=0 dead_lettered=1' "$scenario/worker-reap.txt"
  test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=3 AND attempt=1 AND lease_generation=1 AND owner_node IS NULL AND receipt_digest IS NULL AND dead_reason_digest IS NOT NULL" | tr -d '[:space:]')" = "$expected_dead_letter_count"
  test "$(sql_exec 'SELECT count(*) FROM trnm_outbox WHERE state=1' | tr -d '[:space:]')" = 0
  sql_exec "SELECT state, attempt, lease_generation, owner_node IS NULL, receipt_digest IS NULL, dead_reason_digest IS NOT NULL FROM trnm_outbox ORDER BY intent_id" >"$scenario/database-after-reap.tsv"
  case "$boundary" in
    crash-before-publish)
      test ! -e "$spool_path"
      test "$(find "$spool" -maxdepth 1 -type f -name '*.json' | wc -l | tr -d '[:space:]')" = 0
      ;;
    crash-after-publish)
      sha256sum "$spool_path" >"$scenario/spool-after.sha256"
      test "$(cut -d' ' -f1 "$scenario/spool-before.sha256")" = "$(cut -d' ' -f1 "$scenario/spool-after.sha256")"
      test "$(find "$spool" -maxdepth 1 -type f -name "$intent_hex.json" | wc -l | tr -d '[:space:]')" = 1
      ;;
  esac
}

run_scenario crash-after-publish 144 160 161 145 162 20 70 1
run_scenario crash-before-publish 146 176 177 178 179 30 71 2
test "$(sql_exec 'SELECT count(*) FROM trnm_outbox' | tr -d '[:space:]')" = 2
capture_container_diagnostics

python3 - "$evidence" <<'PY'
import hashlib
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
source = dict(line.split("=", 1) for line in (root / "source.txt").read_text().splitlines())
artifacts = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.name in {"manifest.json", "SHA256SUMS"}:
        continue
    data = path.read_bytes()
    artifacts.append({"path": str(path.relative_to(root)), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
manifest = {
    "schema": "trillionnium.outbox-final-attempt-boundaries-evidence.v2",
    "target_repository": source["repository"], "target_commit": source["commit"],
    "target_tree": source["tree"], "profile": source["profile"], "image": source["image"],
    "migration": source["migration"], "migration_sha256": source["migration_sha256"],
    "run_id": source["run_id"], "max_attempts": 1,
    "scenarios": [
        {"boundary": "crash-after-publish", "worker_exit_code": 70, "durable_visible_effect_count": 1, "reaped_dead_letter_count_reported": 1, "semantics": "durable idempotent effect exists; database acknowledgement is absent; no second visible effect is emitted"},
        {"boundary": "crash-before-publish", "worker_exit_code": 71, "durable_visible_effect_count": 0, "reaped_dead_letter_count_reported": 1, "semantics": "the exhausted lease is terminally dead-lettered and the external effect can be lost"},
    ],
    "assertions": {
        "both_final_attempt_crash_boundaries_executed": True,
        "expired_exhausted_leases_reaped_to_dead_letter": True,
        "reaper_transition_count_exposed_by_worker": True,
        "owner_cleared": True, "receipt_absent": True, "dead_reason_present": True,
        "post_publish_spool_bytes_unchanged": True,
        "post_publish_duplicate_visible_effect_count": 0,
        "pre_publish_visible_effect_count": 0,
        "possible_lost_effect_declared": True,
    },
    "artifacts": artifacts,
    "claims": {
        "single_host_process_failure_proven": True,
        "postgresql_and_cockroachdb_required": True,
        "exactly_once_external_effect_proven": False,
        "multi_node_failover_proven": False, "endurance_proven": False,
        "compatibility_credit": False, "production_ready": False,
    },
    "limitations": [
        "Final-attempt crash before publication can dead-letter an intent without producing its external effect.",
        "The post-publication case proves one stable local spool record, not cross-host exactly-once delivery.",
        "The spool is not yet a concrete realtime, search, notification or provider consumer.",
        "Independent data-integrity review remains required before closing the outbox gap.",
    ],
}
(root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY
find "$evidence" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >"$evidence/SHA256SUMS"
