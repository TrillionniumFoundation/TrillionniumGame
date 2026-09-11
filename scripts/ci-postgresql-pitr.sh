#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${TRNM_REQUIRE_LIVE_DATABASE:?TRNM_REQUIRE_LIVE_DATABASE must be explicit}"
test "$TRNM_REQUIRE_LIVE_DATABASE" = 1
: "${POSTGRES_IMAGE:?POSTGRES_IMAGE is required}"
EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-pitr}
NETWORK=${POSTGRES_NETWORK:-trnm-pitr-network}
PRIMARY=${POSTGRES_PRIMARY_NAME:-trnm-pitr-primary}
RESTORED=${POSTGRES_RESTORED_NAME:-trnm-pitr-restored}
PRIMARY_VOLUME=${POSTGRES_PRIMARY_VOLUME:-trnm-pitr-primary-data}
BASE_VOLUME=${POSTGRES_BASE_VOLUME:-trnm-pitr-base-data}
RESTORE_VOLUME=${POSTGRES_RESTORE_VOLUME:-trnm-pitr-restored-data}
ARCHIVE_VOLUME=${POSTGRES_ARCHIVE_VOLUME:-trnm-pitr-archive}
PRIMARY_PORT=${POSTGRES_PRIMARY_PORT:-55438}
RESTORE_PORT=${POSTGRES_RESTORE_PORT:-55439}
mkdir -p "$EVIDENCE_DIR"

cleanup() {
  docker rm -f "$PRIMARY" "$RESTORED" >/dev/null 2>&1 || :
  docker volume rm -f "$PRIMARY_VOLUME" "$BASE_VOLUME" "$RESTORE_VOLUME" "$ARCHIVE_VOLUME" >/dev/null 2>&1 || :
  docker network rm "$NETWORK" >/dev/null 2>&1 || :
}
trap cleanup EXIT
cleanup
docker pull "$POSTGRES_IMAGE"
docker network create "$NETWORK" >/dev/null
for volume in "$PRIMARY_VOLUME" "$BASE_VOLUME" "$RESTORE_VOLUME" "$ARCHIVE_VOLUME"; do
  docker volume create "$volume" >/dev/null
done
docker run --rm -v "$ARCHIVE_VOLUME:/archive" --entrypoint bash "$POSTGRES_IMAGE" \
  -ceu 'chown -R postgres:postgres /archive'
docker run -d --name "$PRIMARY" --network "$NETWORK" \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=trnm \
  -v "$PRIMARY_VOLUME:/var/lib/postgresql/data" -v "$ARCHIVE_VOLUME:/archive" \
  -p "${PRIMARY_PORT}:5432" "$POSTGRES_IMAGE" postgres \
  -c wal_level=replica -c archive_mode=on \
  -c "archive_command=test ! -f /archive/%f && cp %p /archive/%f" \
  -c archive_timeout=1s >/dev/null
for _ in $(seq 1 90); do
  docker exec "$PRIMARY" pg_isready -U postgres -d trnm >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$PRIMARY" pg_isready -U postgres -d trnm >/dev/null
primary_sql() {
  docker exec "$PRIMARY" psql -v ON_ERROR_STOP=1 -X -q -U postgres -d trnm -c "$1"
}
primary_scalar() {
  docker exec "$PRIMARY" psql -X -q -tA -U postgres -d trnm -c "$1"
}
restored_scalar() {
  docker exec "$RESTORED" psql -X -q -tA -U postgres -d trnm -c "$1"
}
snapshot() {
  local container=$1
  docker exec -i "$container" psql -v ON_ERROR_STOP=1 -X -q -tA \
    -U postgres -d trnm < "$ROOT/scripts/postgresql-semantic-snapshot.sql"
}

mapfile -t migrations < <(find "$ROOT/migrations/postgresql" -maxdepth 1 -type f -name '*_up.sql' | sort)
test "${#migrations[@]}" -gt 0
cat "${migrations[@]}" | docker exec -i "$PRIMARY" psql -v ON_ERROR_STOP=1 \
  -U postgres -d trnm >/dev/null
primary_sql "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replicator'" >/dev/null
docker exec "$PRIMARY" sh -ceu \
  "printf '%s\\n' 'host replication replicator 0.0.0.0/0 scram-sha-256' >> \"\$PGDATA/pg_hba.conf\""
primary_sql "SELECT pg_reload_conf()" >/dev/null
primary_sql "INSERT INTO trnm_schema_metadata VALUES (1,1,'postgresql',repeat('a',40),10)" >/dev/null
primary_sql "INSERT INTO trnm_entity_heads VALUES (decode(repeat('11',16),'hex'),0,0,1,decode(repeat('12',32),'hex'),10)" >/dev/null
primary_sql "CHECKPOINT" >/dev/null

docker run --rm --network "$NETWORK" -e PGPASSWORD=replicator \
  -v "$BASE_VOLUME:/base" --entrypoint bash "$POSTGRES_IMAGE" \
  -ceu "rm -rf /base/*; chown -R postgres:postgres /base; \
         gosu postgres pg_basebackup -h ${PRIMARY} -p 5432 -U replicator \
           -D /base -Fp -Xs -P; chown -R postgres:postgres /base"
docker run --rm -v "$BASE_VOLUME:/base:ro" --entrypoint bash "$POSTGRES_IMAGE" \
  -ceu 'test -s /base/backup_label; test -s /base/PG_VERSION'

primary_sql "INSERT INTO trnm_storage_objects VALUES ('pitr-target','included',decode(repeat('51',16),'hex'),decode('0102','hex'),decode(repeat('52',32),'hex'),2,1,20)" >/dev/null
target_lsn=$(primary_scalar 'SELECT pg_current_wal_flush_lsn()')
test -n "$target_lsn"
printf '%s\n' "$target_lsn" > "$EVIDENCE_DIR/target-lsn.txt"
snapshot "$PRIMARY" > "$EVIDENCE_DIR/target-snapshot.txt"
primary_sql "SELECT pg_switch_wal()" >/dev/null
for _ in $(seq 1 80); do
  archived_count=$(primary_scalar "SELECT archived_count FROM pg_stat_archiver")
  [[ "$archived_count" -ge 1 ]] && break
  sleep 0.25
done
test "$archived_count" -ge 1

primary_sql "INSERT INTO trnm_storage_objects VALUES ('pitr-after-target','excluded',decode(repeat('61',16),'hex'),decode('0304','hex'),decode(repeat('62',32),'hex'),2,1,30)" >/dev/null
test "$(primary_scalar "SELECT count(*) FROM trnm_storage_objects WHERE collection='pitr-after-target'")" = 1
primary_sql "SELECT pg_switch_wal()" >/dev/null
for _ in $(seq 1 80); do
  next_archived_count=$(primary_scalar "SELECT archived_count FROM pg_stat_archiver")
  [[ "$next_archived_count" -ge 2 ]] && break
  sleep 0.25
done
test "$next_archived_count" -ge 2
snapshot "$PRIMARY" > "$EVIDENCE_DIR/final-primary-snapshot.txt"
if cmp --silent "$EVIDENCE_DIR/target-snapshot.txt" "$EVIDENCE_DIR/final-primary-snapshot.txt"; then
  echo "after-target write did not change source snapshot" >&2
  exit 1
fi
docker stop --time 2 "$PRIMARY" >/dev/null

docker run --rm -v "$BASE_VOLUME:/base:ro" -v "$RESTORE_VOLUME:/restore" \
  --entrypoint bash "$POSTGRES_IMAGE" -ceu \
  "cp -a /base/. /restore/; chown -R postgres:postgres /restore; \
   touch /restore/recovery.signal; chown postgres:postgres /restore/recovery.signal; \
   printf '%s\\n' \"restore_command = 'cp /archive/%f %p'\" \
     \"recovery_target_lsn = '${target_lsn}'\" \
     \"recovery_target_inclusive = 'true'\" \
     \"recovery_target_action = 'promote'\" \
     >> /restore/postgresql.auto.conf; \
   chown postgres:postgres /restore/postgresql.auto.conf"
recovery_started_ms=$(date +%s%3N)
docker run -d --name "$RESTORED" --network "$NETWORK" \
  -v "$RESTORE_VOLUME:/var/lib/postgresql/data" -v "$ARCHIVE_VOLUME:/archive:ro" \
  -p "${RESTORE_PORT}:5432" "$POSTGRES_IMAGE" >/dev/null
for _ in $(seq 1 180); do
  if docker exec "$RESTORED" pg_isready -U postgres -d trnm >/dev/null 2>&1; then
    promoted=$(restored_scalar 'SELECT NOT pg_is_in_recovery()' 2>/dev/null || :)
    [[ "$promoted" = t ]] && break
  fi
  sleep 0.5
done
test "$promoted" = t
recovery_finished_ms=$(date +%s%3N)
measured_recovery_ms=$((recovery_finished_ms-recovery_started_ms))
printf '%s\n' "$measured_recovery_ms" > "$EVIDENCE_DIR/measured-recovery-ms.txt"
target_commit_present=$(restored_scalar "SELECT count(*) FROM trnm_storage_objects WHERE collection='pitr-target' AND object_key='included'")
after_target_commit_absent=$(restored_scalar "SELECT count(*) FROM trnm_storage_objects WHERE collection='pitr-after-target'")
test "$target_commit_present" = 1
test "$after_target_commit_absent" = 0
snapshot "$RESTORED" > "$EVIDENCE_DIR/restored-snapshot.txt"
cmp --silent "$EVIDENCE_DIR/target-snapshot.txt" "$EVIDENCE_DIR/restored-snapshot.txt"
archive_file_count=$(docker run --rm -v "$ARCHIVE_VOLUME:/archive:ro" --entrypoint sh "$POSTGRES_IMAGE" \
  -ceu "find /archive -type f | wc -l")
test "$archive_file_count" -ge 2
printf '%s\n' "$archive_file_count" > "$EVIDENCE_DIR/archive-file-count.txt"
docker inspect --format='{{.Image}}' "$PRIMARY" > "$EVIDENCE_DIR/primary-image-id.txt"
docker inspect --format='{{.Image}}' "$RESTORED" > "$EVIDENCE_DIR/restored-image-id.txt"
rm -rf "$EVIDENCE_DIR/archive-files"
docker run --rm -v "$ARCHIVE_VOLUME:/archive:ro" -v "$EVIDENCE_DIR:/evidence" \
  --entrypoint bash "$POSTGRES_IMAGE" -ceu 'cp -a /archive /evidence/archive-files'

python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" "$target_lsn" "$measured_recovery_ms" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]); evidence=Path(sys.argv[2])
def digest(path:Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()
def tree_digest(path:Path)->str:
  h=hashlib.sha256(); files=sorted(p for p in path.rglob('*') if p.is_file())
  if not files: raise SystemExit('WAL archive is empty')
  for p in files:
    r=p.relative_to(path).as_posix().encode(); b=p.read_bytes(); h.update(len(r).to_bytes(8,'big')); h.update(r); h.update(len(b).to_bytes(8,'big')); h.update(b)
  return h.hexdigest()
manifest={
  "schema":"trillionnium.postgresql-pitr-evidence.v1",
  "profile":"postgresql",
  "image_reference":sys.argv[3],
  "primary_image_id":(evidence/"primary-image-id.txt").read_text().strip(),
  "restored_image_id":(evidence/"restored-image-id.txt").read_text().strip(),
  "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
  "target_lsn":sys.argv[4],
  "measured_recovery_ms":int(sys.argv[5]),
  "wal_archive_sha256":tree_digest(evidence/"archive-files"),
  "target_snapshot_sha256":digest(evidence/"target-snapshot.txt"),
  "restored_snapshot_sha256":digest(evidence/"restored-snapshot.txt"),
  "target_commit_present": True,
  "after_target_commit_absent": True,
  "semantic_snapshot_equal":True,
  "claim_boundary":{
    "approved_rpo_rto": False,
    "continuous_archive_monitoring_proven":False,
    "regional_restore_proven":False,
    "accepted_evidence": False,
    "independently_accepted":False,
    "production_ready": False,
  },
}
if manifest["target_snapshot_sha256"] != manifest["restored_snapshot_sha256"]: raise SystemExit('PITR semantic mismatch')
(evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
print(json.dumps(manifest,sort_keys=True))
PY
python3 "$ROOT/scripts/check-postgresql-pitr.py"
