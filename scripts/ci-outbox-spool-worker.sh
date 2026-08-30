#!/usr/bin/env bash
set -Eeuo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *)
    echo 'usage: ci-outbox-spool-worker.sh postgresql|cockroachdb' >&2
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
evidence_root=${TRNM_EVIDENCE_ROOT:-run/outbox-spool-worker}
evidence="$evidence_root/$profile/$run_id"
spool="$evidence/spool"
mkdir -p "$evidence/logs" "$spool"
container="trnm-outbox-${profile}-${run_id//[^a-zA-Z0-9_.-]/-}"
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
  port=26257
  database_url="postgresql://root@127.0.0.1:${port}/trnm?sslmode=disable"
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
export TRNM_OUTBOX_NODE_ID_HEX=$(printf 'aa%.0s' {1..16})
export TRNM_OUTBOX_SPOOL_DIRECTORY="$spool"
export TRNM_OUTBOX_BATCH_SIZE=16
export TRNM_OUTBOX_LEASE_DURATION_MS=1000
export TRNM_OUTBOX_MAX_ATTEMPTS=8
export TRNM_OUTBOX_POLL_INTERVAL_MS=10
export TRNM_OUTBOX_MAX_BACKOFF_MS=1000

command_bin=target/debug/trnm-pg-command
worker_bin=target/debug/trnm-outbox-worker

"$worker_bin" check-config >"$evidence/worker-config.txt"
grep -q '<redacted>' "$evidence/worker-config.txt"
if grep -q "$password" "$evidence/worker-config.txt"; then
  echo 'worker check-config leaked database credentials' >&2
  exit 1
fi

# Scenario 1: pending -> leased -> durable spool -> completed.
"$command_bin" bootstrap \
  --entity-byte 17 --authority-generation 1 --state-byte 18 --updated-at-ms 10 \
  >"$evidence/bootstrap-normal.json"
"$command_bin" apply \
  --entity-byte 17 --command-byte 33 --fingerprint-byte 34 \
  --expected-revision 0 --authority-generation 1 --state-byte 35 --committed-at-ms 20 \
  >"$evidence/apply-normal.json"
"$worker_bin" run-once | tee "$evidence/worker-normal.txt"
grep -q 'claimed=1 completed=1 retried=0 dead_lettered=0' \
  "$evidence/worker-normal.txt"

python3 - "$spool" 17 33 35 normal <<'PY'
import hashlib
import pathlib
import sys

spool = pathlib.Path(sys.argv[1])
entity = int(sys.argv[2])
command = int(sys.argv[3])
intent = int(sys.argv[4])
label = sys.argv[5]
hex16 = lambda value: f"{value:02x}" * 16
hex32 = lambda value: f"{value:02x}" * 32
expected = (
    '{"schema":"trillionnium.outbox-spool.v1",'
    f'"intent_id":"{hex16(intent)}",'
    f'"entity_id":"{hex16(entity)}",'
    f'"command_id":"{hex16(command)}",'
    '"kind":"broadcast",'
    f'"payload_digest":"{hex32(intent)}"}}\n'
).encode()
path = spool / f"{hex16(intent)}.json"
actual = path.read_bytes()
assert actual == expected, (path, actual, expected)
(spool.parent / f"{label}-receipt-sha256.txt").write_text(
    hashlib.sha256(actual).hexdigest() + "\n",
    encoding="utf-8",
)
PY

test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=2 AND receipt_digest IS NOT NULL AND owner_node IS NULL" | tr -d '[:space:]')" = 1

# Scenario 2: durable write succeeds, acknowledgement is lost, then a new lease
# owner reclaims the row and validates the same stable spool bytes.
"$command_bin" bootstrap \
  --entity-byte 48 --authority-generation 1 --state-byte 49 --updated-at-ms 30 \
  >"$evidence/bootstrap-reclaim.json"
"$command_bin" apply \
  --entity-byte 48 --command-byte 64 --fingerprint-byte 65 \
  --expected-revision 0 --authority-generation 1 --state-byte 66 --committed-at-ms 40 \
  >"$evidence/apply-reclaim.json"
sql_exec "UPDATE trnm_outbox SET state=1, owner_node=entity_id, attempt=1, lease_generation=1, available_at_ms=0, updated_at_ms=0 WHERE state=0" \
  >"$evidence/force-expired-lease.txt"

python3 - "$spool" 48 64 66 reclaim <<'PY'
import hashlib
import pathlib
import sys

spool = pathlib.Path(sys.argv[1])
entity = int(sys.argv[2])
command = int(sys.argv[3])
intent = int(sys.argv[4])
label = sys.argv[5]
hex16 = lambda value: f"{value:02x}" * 16
hex32 = lambda value: f"{value:02x}" * 32
record = (
    '{"schema":"trillionnium.outbox-spool.v1",'
    f'"intent_id":"{hex16(intent)}",'
    f'"entity_id":"{hex16(entity)}",'
    f'"command_id":"{hex16(command)}",'
    '"kind":"broadcast",'
    f'"payload_digest":"{hex32(intent)}"}}\n'
).encode()
path = spool / f"{hex16(intent)}.json"
path.write_bytes(record)
(spool.parent / f"{label}-preexisting-sha256.txt").write_text(
    hashlib.sha256(record).hexdigest() + "\n",
    encoding="utf-8",
)
PY

export TRNM_OUTBOX_NODE_ID_HEX=$(printf 'bb%.0s' {1..16})
"$worker_bin" run-once | tee "$evidence/worker-reclaim.txt"
grep -q 'claimed=1 completed=1 retried=0 dead_lettered=0' \
  "$evidence/worker-reclaim.txt"
test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=2" | tr -d '[:space:]')" = 2
test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=2 AND attempt=2 AND lease_generation=2 AND owner_node IS NULL" | tr -d '[:space:]')" = 1

python3 - "$spool" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).parent
before = (root / "reclaim-preexisting-sha256.txt").read_text().strip()
path = pathlib.Path(sys.argv[1]) / (("42" * 16) + ".json")
after = hashlib.sha256(path.read_bytes()).hexdigest()
assert before == after, (before, after)
(root / "reclaim-final-sha256.txt").write_text(after + "\n", encoding="utf-8")
PY

# Scenario 3: a conflicting durable receipt cannot be overwritten and reaches
# the atomic dead-letter terminal state at the configured attempt limit.
"$command_bin" bootstrap \
  --entity-byte 80 --authority-generation 1 --state-byte 81 --updated-at-ms 50 \
  >"$evidence/bootstrap-conflict.json"
"$command_bin" apply \
  --entity-byte 80 --command-byte 96 --fingerprint-byte 97 \
  --expected-revision 0 --authority-generation 1 --state-byte 98 --committed-at-ms 60 \
  >"$evidence/apply-conflict.json"
printf 'conflicting durable bytes\n' >"$spool/$(printf '62%.0s' {1..16}).json"
export TRNM_OUTBOX_MAX_ATTEMPTS=1
export TRNM_OUTBOX_NODE_ID_HEX=$(printf 'cc%.0s' {1..16})
"$worker_bin" run-once | tee "$evidence/worker-conflict.txt"
grep -q 'claimed=1 completed=0 retried=0 dead_lettered=1' \
  "$evidence/worker-conflict.txt"
test "$(cat "$spool/$(printf '62%.0s' {1..16}).json")" = 'conflicting durable bytes'
test "$(sql_exec "SELECT count(*) FROM trnm_outbox WHERE state=3 AND dead_reason_digest IS NOT NULL AND owner_node IS NULL" | tr -d '[:space:]')" = 1

test "$(sql_exec 'SELECT count(*) FROM trnm_outbox' | tr -d '[:space:]')" = 3
test "$(sql_exec 'SELECT count(*) FROM trnm_outbox WHERE state=2' | tr -d '[:space:]')" = 2
test "$(sql_exec 'SELECT count(*) FROM trnm_outbox WHERE state=3' | tr -d '[:space:]')" = 1

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
    "schema": "trillionnium.outbox-spool-worker-evidence.v1",
    "target_repository": source["repository"],
    "target_commit": source["commit"],
    "target_tree": source["tree"],
    "profile": source["profile"],
    "image": source["image"],
    "migration": source["migration"],
    "migration_sha256": source["migration_sha256"],
    "run_id": source["run_id"],
    "assertions": {
        "normal_delivery_completed": True,
        "stable_receipt_digest": True,
        "post_write_pre_ack_reclaim_completed": True,
        "lease_owner_and_generation_fenced": True,
        "conflicting_receipt_not_overwritten": True,
        "attempt_exhaustion_dead_lettered": True,
        "completed_count": 2,
        "dead_letter_count": 1,
    },
    "artifacts": artifacts,
    "claims": {
        "single_node_dual_profile_source_slice_executed": True,
        "external_effect_provider_executed": False,
        "multi_node_failover_proven": False,
        "endurance_proven": False,
        "compatibility_credit": False,
        "production_ready": False,
    },
    "limitations": [
        "The sink is a durable local spool, not a provider or realtime consumer.",
        "The run is single-node and does not prove cross-host filesystem semantics.",
        "Independent database/data-integrity review is still required for gap credit.",
    ],
}
(root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY

find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum >"$evidence/SHA256SUMS"
