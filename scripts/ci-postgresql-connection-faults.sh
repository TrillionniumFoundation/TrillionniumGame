#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${TRNM_REQUIRE_LIVE_DATABASE:?TRNM_REQUIRE_LIVE_DATABASE must be explicit}"
test "$TRNM_REQUIRE_LIVE_DATABASE" = 1
: "${POSTGRES_IMAGE:?POSTGRES_IMAGE is required}"
EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-connection-faults}
CONTAINER=${POSTGRES_CONTAINER_NAME:-trnm-connection-fault-pg}
PORT=${POSTGRES_PORT:-55435}
mkdir -p "$EVIDENCE_DIR"
background_pids=()

cleanup() {
  for pid in "${background_pids[@]:-}"; do
    kill "$pid" >/dev/null 2>&1 || :
  done
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || :
}
trap cleanup EXIT
cleanup
docker pull "$POSTGRES_IMAGE"
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm \
  -p "${PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
for _ in $(seq 1 90); do
  docker exec "$CONTAINER" pg_isready -U trnm -d trnm >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U trnm -d trnm >/dev/null
mapfile -t migrations < <(find "$ROOT/migrations/postgresql" -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#migrations[@]}" -gt 0
cat "${migrations[@]}" | docker exec -i "$CONTAINER" \
  psql -v ON_ERROR_STOP=1 -U trnm -d trnm >/dev/null

cat <<'SQL' | docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='trnm_runtime_fault') THEN
    CREATE ROLE trnm_runtime_fault LOGIN PASSWORD 'trnm_runtime_fault' CONNECTION LIMIT 4;
  END IF;
END
$$;
ALTER ROLE trnm_runtime_fault CONNECTION LIMIT 4;
GRANT CONNECT ON DATABASE trnm TO trnm_runtime_fault;
GRANT USAGE ON SCHEMA public TO trnm_runtime_fault;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trnm_runtime_fault;
INSERT INTO trnm_entity_heads VALUES
  (decode(repeat('b1',16),'hex'), 0, 0, 1, decode(repeat('b2',32),'hex'), 10);
SQL

runtime_psql() {
  PGPASSWORD=trnm_runtime_fault psql -v ON_ERROR_STOP=1 -X -q \
    -h 127.0.0.1 -p "$PORT" -U trnm_runtime_fault -d trnm "$@"
}
admin_scalar() {
  docker exec "$CONTAINER" psql -X -q -tA -U trnm -d trnm -c "$1"
}

: > "$EVIDENCE_DIR/connection-churn.txt"
for index in $(seq 1 80); do
  value=$(PGAPPNAME="trnm-churn-${index}" runtime_psql -tA -c 'SELECT 1')
  test "$value" = 1
  printf '%s|%s\n' "$index" "$value" >> "$EVIDENCE_DIR/connection-churn.txt"
done
test "$(wc -l < "$EVIDENCE_DIR/connection-churn.txt")" = 80

for index in $(seq 1 4); do
  PGAPPNAME="trnm-hold-${index}" runtime_psql -c 'SELECT pg_sleep(30)' \
    > "$EVIDENCE_DIR/hold-${index}.stdout" \
    2> "$EVIDENCE_DIR/hold-${index}.stderr" &
  background_pids+=("$!")
done
for _ in $(seq 1 40); do
  held=$(admin_scalar "SELECT count(*) FROM pg_stat_activity WHERE usename='trnm_runtime_fault' AND application_name LIKE 'trnm-hold-%'")
  [[ "$held" = 4 ]] && break
  sleep 0.25
done
test "$held" = 4
if PGAPPNAME=trnm-pool-exhaustion runtime_psql -c 'SELECT 1' \
     > "$EVIDENCE_DIR/pool-exhaustion.stdout" \
     2> "$EVIDENCE_DIR/pool-exhaustion.stderr"; then
  echo "pool-exhaustion connection unexpectedly succeeded" >&2
  exit 1
fi
grep -Ei 'too many connections|connection limit' "$EVIDENCE_DIR/pool-exhaustion.stderr" >/dev/null
terminated_holds=$(admin_scalar "SELECT count(*) FROM (SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='trnm_runtime_fault' AND application_name LIKE 'trnm-hold-%') AS stopped")
test "$terminated_holds" = 4
set +e
for pid in "${background_pids[@]}"; do wait "$pid"; done
set -e
background_pids=()

original_digest=$(admin_scalar "SELECT encode(state_digest,'hex') FROM trnm_entity_heads WHERE entity_id=decode(repeat('b1',16),'hex')")
cat > "$EVIDENCE_DIR/cancel-transaction.sql" <<'SQL'
BEGIN;
UPDATE trnm_entity_heads
SET state_digest=decode(repeat('c1',32),'hex'), updated_at_ms=20
WHERE entity_id=decode(repeat('b1',16),'hex');
SELECT pg_sleep(30);
COMMIT;
SQL
PGAPPNAME=trnm-cancel-transaction runtime_psql -f "$EVIDENCE_DIR/cancel-transaction.sql" \
  > "$EVIDENCE_DIR/cancel-transaction.stdout" \
  2> "$EVIDENCE_DIR/cancel-transaction.stderr" &
cancel_client=$!
background_pids=("$cancel_client")
cancel_pid=''
for _ in $(seq 1 60); do
  cancel_pid=$(admin_scalar "SELECT pid FROM pg_stat_activity WHERE application_name='trnm-cancel-transaction' AND state='active' LIMIT 1")
  [[ -n "$cancel_pid" ]] && break
  sleep 0.25
done
test -n "$cancel_pid"
test "$(admin_scalar "SELECT pg_cancel_backend(${cancel_pid})")" = t
set +e
wait "$cancel_client"
cancel_status=$?
set -e
background_pids=()
test "$cancel_status" -ne 0
grep -Ei 'canceling statement|cancelled statement' "$EVIDENCE_DIR/cancel-transaction.stderr" >/dev/null
cancel_digest=$(admin_scalar "SELECT encode(state_digest,'hex') FROM trnm_entity_heads WHERE entity_id=decode(repeat('b1',16),'hex')")
test "$cancel_digest" = "$original_digest"

cat > "$EVIDENCE_DIR/terminate-transaction.sql" <<'SQL'
BEGIN;
UPDATE trnm_entity_heads
SET state_digest=decode(repeat('d1',32),'hex'), updated_at_ms=30
WHERE entity_id=decode(repeat('b1',16),'hex');
SELECT pg_sleep(30);
COMMIT;
SQL
PGAPPNAME=trnm-terminate-transaction runtime_psql -f "$EVIDENCE_DIR/terminate-transaction.sql" \
  > "$EVIDENCE_DIR/terminate-transaction.stdout" \
  2> "$EVIDENCE_DIR/terminate-transaction.stderr" &
terminate_client=$!
background_pids=("$terminate_client")
terminate_pid=''
for _ in $(seq 1 60); do
  terminate_pid=$(admin_scalar "SELECT pid FROM pg_stat_activity WHERE application_name='trnm-terminate-transaction' AND state='active' LIMIT 1")
  [[ -n "$terminate_pid" ]] && break
  sleep 0.25
done
test -n "$terminate_pid"
test "$(admin_scalar "SELECT pg_terminate_backend(${terminate_pid})")" = t
set +e
wait "$terminate_client"
terminate_status=$?
set -e
background_pids=()
test "$terminate_status" -ne 0
terminate_digest=$(admin_scalar "SELECT encode(state_digest,'hex') FROM trnm_entity_heads WHERE entity_id=decode(repeat('b1',16),'hex')")
test "$terminate_digest" = "$original_digest"

if PGAPPNAME=trnm-statement-timeout runtime_psql \
     -c "SET statement_timeout='400ms'; SELECT pg_sleep(5)" \
     > "$EVIDENCE_DIR/statement-timeout.stdout" \
     2> "$EVIDENCE_DIR/statement-timeout.stderr"; then
  echo "statement_timeout query unexpectedly succeeded" >&2
  exit 1
fi
grep -Ei 'statement timeout|canceling statement' "$EVIDENCE_DIR/statement-timeout.stderr" >/dev/null
fresh_connection_after_faults=$(PGAPPNAME=trnm-fresh-connection-after-faults runtime_psql -tA -c 'SELECT 1')
test "$fresh_connection_after_faults" = 1
printf '%s\n' "$fresh_connection_after_faults" > "$EVIDENCE_DIR/fresh-connection-after-faults.txt"

docker inspect --format='{{.Image}}' "$CONTAINER" > "$EVIDENCE_DIR/image-id.txt"
python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root=Path(sys.argv[1])
evidence=Path(sys.argv[2])
def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest={
    "schema":"trillionnium.postgresql-connection-fault-evidence.v1",
    "profile":"postgresql",
    "image_reference":sys.argv[3],
    "image_id":(evidence/"image-id.txt").read_text().strip(),
    "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
    "connection_churn_count":80,
    "pool_exhaustion_rejected":True,
    "cancel_rollback_verified":True,
    "terminate_rollback_verified":True,
    "statement_timeout_verified":True,
    "fresh_connection_after_faults":True,
    "churn_sha256":digest(evidence/"connection-churn.txt"),
    "cancel_error_sha256":digest(evidence/"cancel-transaction.stderr"),
    "terminate_error_sha256":digest(evidence/"terminate-transaction.stderr"),
    "claim_boundary":{
        "application_pool_exhaustion_proven":False,
        "multi_node_failover_proven":False,
        "accepted_evidence": False,
        "independently_accepted":False,
        "performance_accepted":False,
        "production_ready": False,
    },
}
(evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print(json.dumps(manifest,sort_keys=True))
PY
python3 "$ROOT/scripts/check-postgresql-connection-faults.py"
