#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${POSTGRES_IMAGE:?POSTGRES_IMAGE is required}"
EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-recovery-barrier}
CONTAINER=${POSTGRES_CONTAINER_NAME:-trnm-recovery-barrier-pg}
PORT=${POSTGRES_PORT:-55434}
mkdir -p "$EVIDENCE_DIR"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || :
}
trap cleanup EXIT
cleanup
docker pull "$POSTGRES_IMAGE"
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm_source \
  -p "${PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
for _ in $(seq 1 90); do
  docker exec "$CONTAINER" pg_isready -U trnm -d trnm_source >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U trnm -d trnm_source >/dev/null
mapfile -t migrations < <(find "$ROOT/migrations/postgresql" -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#migrations[@]}" -gt 0
cat "${migrations[@]}" | docker exec -i "$CONTAINER" \
  psql -v ON_ERROR_STOP=1 -U trnm -d trnm_source >/dev/null

cat <<'SQL' | docker exec -i "$CONTAINER" \
  psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm_source
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trnm_runtime') THEN
    CREATE ROLE trnm_runtime LOGIN PASSWORD 'trnm_runtime';
  END IF;
END
$$;
GRANT CONNECT ON DATABASE trnm_source TO trnm_runtime;
GRANT USAGE ON SCHEMA public TO trnm_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO trnm_runtime;
INSERT INTO trnm_entity_heads VALUES
  (decode(repeat('11',16),'hex'), 1, 1, 1, decode(repeat('12',32),'hex'), 10);
INSERT INTO trnm_command_receipts VALUES
  (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'),
   decode(repeat('23',32),'hex'), 1, decode(repeat('24',32),'hex'), 1, 1, 1, 10);
INSERT INTO trnm_outbox VALUES
  (decode(repeat('41',16),'hex'), decode(repeat('11',16),'hex'),
   decode(repeat('22',16),'hex'), 3, decode(repeat('42',32),'hex'),
   0, 0, 0, NULL, NULL, NULL, 10, 10);
INSERT INTO trnm_outbox VALUES
  (decode(repeat('43',16),'hex'), decode(repeat('11',16),'hex'),
   decode(repeat('22',16),'hex'), 3, decode(repeat('44',32),'hex'),
   1, 1, 1, decode(repeat('45',16),'hex'), NULL, NULL, 10, 10);
INSERT INTO trnm_command_outbox VALUES
  (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'), 0,
   decode(repeat('41',16),'hex'));
INSERT INTO trnm_command_outbox VALUES
  (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'), 1,
   decode(repeat('43',16),'hex'));
SQL

if docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm \
     -d trnm_source < "$ROOT/scripts/postgresql-recovery-quarantine.sql" \
     > "$EVIDENCE_DIR/active-lease-rejection.stdout" \
     2> "$EVIDENCE_DIR/active-lease-rejection.stderr"; then
  echo "active-lease-rejection unexpectedly succeeded" >&2
  exit 1
fi
grep -F 'active outbox lease blocks recovery quarantine' \
  "$EVIDENCE_DIR/active-lease-rejection.stderr" >/dev/null
pending_before=$(docker exec "$CONTAINER" psql -X -q -tA -U trnm -d trnm_source \
  -c 'SELECT count(*) FROM trnm_outbox WHERE state IN (0,1)')
test "$pending_before" = 2

docker exec "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm -d trnm_source \
  -c "UPDATE trnm_outbox SET state=0, owner_node=NULL, lease_generation=2, updated_at_ms=11 WHERE state=1" \
  >/dev/null
docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -X -q -U trnm \
  -d trnm_source < "$ROOT/scripts/postgresql-recovery-quarantine.sql" \
  > "$EVIDENCE_DIR/quarantine-export.csv"
test -s "$EVIDENCE_DIR/quarantine-export.csv"
grep -F '41414141414141414141414141414141' "$EVIDENCE_DIR/quarantine-export.csv" >/dev/null
grep -F '43434343434343434343434343434343' "$EVIDENCE_DIR/quarantine-export.csv" >/dev/null

pending_or_leased_after_quarantine=$(docker exec "$CONTAINER" psql -X -q -tA \
  -U trnm -d trnm_source -c 'SELECT count(*) FROM trnm_outbox WHERE state IN (0,1)')
test "$pending_or_leased_after_quarantine" = 0
quarantined=$(docker exec "$CONTAINER" psql -X -q -tA -U trnm -d trnm_source \
  -c "SELECT count(*) FROM trnm_outbox WHERE state=3 AND dead_reason_digest=decode(repeat('f0',32),'hex')")
test "$quarantined" = 2

runtime_read_after_fence=$(PGPASSWORD=trnm_runtime psql -X -q -tA \
  -h 127.0.0.1 -p "$PORT" -U trnm_runtime -d trnm_source \
  -c 'SELECT count(*) FROM trnm_outbox')
test "$runtime_read_after_fence" = 2
printf '%s\n' "$runtime_read_after_fence" > "$EVIDENCE_DIR/runtime-read-after-fence.txt"
if PGPASSWORD=trnm_runtime psql -v ON_ERROR_STOP=1 -X -q \
     -h 127.0.0.1 -p "$PORT" -U trnm_runtime -d trnm_source \
     -c "INSERT INTO trnm_storage_objects VALUES ('fence','blocked',decode(repeat('77',16),'hex'),decode('01','hex'),decode(repeat('78',32),'hex'),2,1,20)" \
     > "$EVIDENCE_DIR/runtime-write-after-fence.stdout" \
     2> "$EVIDENCE_DIR/runtime-write-after-fence.stderr"; then
  echo "read-only runtime write unexpectedly succeeded" >&2
  exit 1
fi
grep -Ei 'read-only|read only' "$EVIDENCE_DIR/runtime-write-after-fence.stderr" >/dev/null

docker inspect --format='{{.Image}}' "$CONTAINER" > "$EVIDENCE_DIR/image-id.txt"
python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" "$pending_before" "$quarantined" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root=Path(sys.argv[1])
evidence=Path(sys.argv[2])
def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest={
    "schema":"trillionnium.postgresql-recovery-barrier-evidence.v1",
    "profile":"postgresql",
    "image_reference":sys.argv[3],
    "image_id":(evidence/"image-id.txt").read_text().strip(),
    "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
    "active_lease_rejected": True,
    "pending_before":int(sys.argv[4]),
    "quarantined":int(sys.argv[5]),
    "pending_or_leased_after_quarantine":0,
    "quarantine_export_sha256":digest(evidence/"quarantine-export.csv"),
    "runtime_write_error_sha256":digest(evidence/"runtime-write-after-fence.stderr"),
    "write_fence_effective_for_new_runtime_connections": True,
    "claim_boundary": {
        "routing_fence_proven": False,
        "all_existing_connections_fenced": False,
        "accepted_evidence": False,
        "independently_accepted": False,
        "rollback_authorized": False,
        "production_ready": False,
    },
}
(evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print(json.dumps(manifest,sort_keys=True))
PY
python3 "$ROOT/scripts/check-postgresql-recovery-barrier.py"
