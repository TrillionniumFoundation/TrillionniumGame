#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${POSTGRES_IMAGE:?POSTGRES_IMAGE must bind an immutable or recorded image reference}"
expected_image=$(python3 - "$ROOT/config/database-test-images.json" <<'PY_IMAGE'
import json,sys
print(json.load(open(sys.argv[1]))['profiles']['postgresql']['image'])
PY_IMAGE
)
if [[ "$POSTGRES_IMAGE" != "$expected_image" ]]; then
  echo 'postgresql image must match config/database-test-images.json' >&2
  exit 64
fi
EVIDENCE_DIR=${EVIDENCE_DIR:-$ROOT/.artifacts/postgresql-semantic-recovery}
CONTAINER=${POSTGRES_CONTAINER_NAME:-trnm-semantic-recovery-pg}
PORT=${POSTGRES_PORT:-55433}
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

TRNM_DATABASE_URL="postgresql://trnm:trnm@127.0.0.1:${PORT}/trnm_source" TRNM_DATABASE_PROFILE=postgresql \
  bash "$ROOT/scripts/apply-authoritative-schema.sh" migrate \
  > "$EVIDENCE_DIR/schema-identity.json" 2> "$EVIDENCE_DIR/schema-build.log"
cp "$ROOT/migrations/MIGRATION_CHAIN.lock.json" "$EVIDENCE_DIR/migration-lock.json"
python3 "$ROOT/scripts/check-migration-lock.py" > "$EVIDENCE_DIR/migration-chain-validation.json"
python3 "$ROOT/scripts/check-authoritative-schema-identity.py" "$EVIDENCE_DIR/schema-identity.json" postgresql --mode fresh \
  > "$EVIDENCE_DIR/schema-identity-check.json"

psql_file() {
  local database=$1
  local file=$2
  docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -v VERBOSITY=verbose -X -q -U trnm -d "$database" \
    < "$file"
}
psql_command() {
  local database=$1
  local sql=$2
  docker exec "$CONTAINER" psql -v ON_ERROR_STOP=1 -v VERBOSITY=verbose -X -q -U trnm -d "$database" \
    -c "$sql"
}

psql_file trnm_source "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
  > "$EVIDENCE_DIR/catalog-before-repeat.txt"
if cat "$ROOT/migrations/postgresql/0001_foundation_up.sql" | docker exec -i "$CONTAINER" \
     psql -v ON_ERROR_STOP=1 -v VERBOSITY=verbose -X -q -U trnm -d trnm_source \
     > "$EVIDENCE_DIR/repeat-migration.stdout" \
     2> "$EVIDENCE_DIR/repeat-migration.stderr"; then
  echo "repeat-migration unexpectedly succeeded" >&2
  exit 1
fi
python3 "$ROOT/scripts/check-sql-error.py" --stderr "$EVIDENCE_DIR/repeat-migration.stderr" --sqlstate 42P07
psql_file trnm_source "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
  > "$EVIDENCE_DIR/catalog-after-repeat.txt"
cmp --silent "$EVIDENCE_DIR/catalog-before-repeat.txt" \
  "$EVIDENCE_DIR/catalog-after-repeat.txt"
TRNM_DATABASE_URL="postgresql://trnm:trnm@127.0.0.1:${PORT}/trnm_source" TRNM_DATABASE_PROFILE=postgresql \
  bash "$ROOT/scripts/apply-authoritative-schema.sh" migrate \
  > "$EVIDENCE_DIR/repeat-schema-identity.json" 2>> "$EVIDENCE_DIR/schema-build.log"
python3 - "$EVIDENCE_DIR/repeat-schema-identity.json" <<'PY_REPEAT'
import json,sys
report=json.load(open(sys.argv[1]))
assert report['schema_version']==2 and report['storage_writer_epoch']==2
assert report['migration_applied'] is False and report['applied_steps']==0
PY_REPEAT


cat <<'SQL' | docker exec -i "$CONTAINER" \
  psql -v ON_ERROR_STOP=1 -v VERBOSITY=verbose -X -q -U trnm -d trnm_source
-- Schema identity was published by the shared Rust migrator.
INSERT INTO trnm_entity_heads VALUES
  (decode(repeat('11',16),'hex'), 1, 1, 1, decode(repeat('12',32),'hex'), 10);
INSERT INTO trnm_entity_heads VALUES
  (decode(repeat('1a',16),'hex'), 0, 0, 1, decode(repeat('1b',32),'hex'), 10);
INSERT INTO trnm_command_receipts VALUES
  (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'),
   decode(repeat('23',32),'hex'), 1, decode(repeat('24',32),'hex'), 1, 1, 1, 10);
INSERT INTO trnm_events VALUES
  (decode(repeat('11',16),'hex'), 1, decode(repeat('33',16),'hex'),
   decode(repeat('22',16),'hex'), decode(repeat('34',32),'hex'), 10);
INSERT INTO trnm_outbox VALUES
  (decode(repeat('44',16),'hex'), decode(repeat('11',16),'hex'),
   decode(repeat('22',16),'hex'), 0, decode(repeat('45',32),'hex'),
   0, 0, 0, NULL, NULL, NULL, 10, 10);
INSERT INTO trnm_command_outbox VALUES
  (decode(repeat('11',16),'hex'), decode(repeat('22',16),'hex'), 0,
   decode(repeat('44',16),'hex'));
INSERT INTO trnm_authority_leases VALUES
  (decode(repeat('11',16),'hex'), decode(repeat('55',16),'hex'), 1, 1, 1000, 10);
INSERT INTO trnm_session_families VALUES
  (decode(repeat('66',16),'hex'), decode(repeat('77',16),'hex'), 0,
   decode(repeat('88',16),'hex'), NULL, 10, 10);
INSERT INTO trnm_refresh_tokens VALUES
  (decode(repeat('66',16),'hex'), decode(repeat('88',16),'hex'),
   decode(repeat('89',32),'hex'), 0, 0, 10, NULL);
INSERT INTO trnm_storage_objects (collection, object_key, user_id, value_bytes, version_digest, read_permission, write_permission, updated_at_ms) VALUES
  ('recovery', 'fixture', decode(repeat('77',16),'hex'), decode('010203','hex'),
   decode(repeat('99',32),'hex'), 2, 1, 10);
SQL

# Known microseconds and unknown v1 history must both survive the restore.
psql_command trnm_source "INSERT INTO trnm_storage_objects
  (collection, object_key, user_id, value_bytes, version_digest, read_permission, write_permission, updated_at_ms, create_time, update_time)
  SELECT collection, 'known-time', user_id, value_bytes, version_digest, read_permission, write_permission, updated_at_ms,
    '1969-12-31 23:59:59.999999+00'::TIMESTAMPTZ, '2024-02-29 00:00:00.123456+00'::TIMESTAMPTZ
  FROM trnm_storage_objects WHERE object_key <> 'known-time' LIMIT 1" >/dev/null

negative_constraint() {
  local label=$1
  local expected_state=$2
  local expected_constraint=$3
  local sql=$4
  if psql_command trnm_source "$sql" \
       > "$EVIDENCE_DIR/negative-constraint-${label}.stdout" \
       2> "$EVIDENCE_DIR/negative-constraint-${label}.stderr"; then
    echo "negative-constraint ${label} unexpectedly succeeded" >&2
    exit 1
  fi
  python3 "$ROOT/scripts/check-sql-error.py" \
    --stderr "$EVIDENCE_DIR/negative-constraint-${label}.stderr" \
    --sqlstate "$expected_state" --constraint "$expected_constraint"
}
negative_constraint metadata-singleton 23514 "trnm_schema_metadata_singleton_check" \
  "INSERT INTO trnm_schema_metadata (singleton, schema_version, profile, source_commit, applied_at_ms) VALUES (2,1,'postgresql',repeat('b',40),10)"
negative_constraint entity-id-width 23514 "trnm_entity_heads_entity_id_check" \
  "INSERT INTO trnm_entity_heads VALUES (decode('01','hex'),0,0,1,decode(repeat('02',32),'hex'),10)"
negative_constraint receipt-event-range 23514 "trnm_command_receipts_check" \
  "INSERT INTO trnm_command_receipts VALUES (decode(repeat('11',16),'hex'),decode(repeat('2a',16),'hex'),decode(repeat('2b',32),'hex'),2,decode(repeat('2c',32),'hex'),NULL,1,1,10)"
negative_constraint event-foreign-key 23503 "trnm_events_entity_id_command_id_fkey" \
  "INSERT INTO trnm_events VALUES (decode(repeat('11',16),'hex'),2,decode(repeat('3a',16),'hex'),decode(repeat('3b',16),'hex'),decode(repeat('3c',32),'hex'),10)"
negative_constraint outbox-state-shape 23514 "trnm_outbox_check" \
  "INSERT INTO trnm_outbox VALUES (decode(repeat('4a',16),'hex'),decode(repeat('11',16),'hex'),decode(repeat('22',16),'hex'),0,decode(repeat('4b',32),'hex'),0,1,1,NULL,NULL,NULL,10,10)"
negative_constraint command-outbox-position 23514 "trnm_command_outbox_position_check" \
  "INSERT INTO trnm_command_outbox VALUES (decode(repeat('11',16),'hex'),decode(repeat('22',16),'hex'),64,decode(repeat('44',16),'hex'))"
negative_constraint lease-generation 23514 "trnm_authority_leases_lease_generation_check" \
  "INSERT INTO trnm_authority_leases VALUES (decode(repeat('1a',16),'hex'),decode(repeat('5a',16),'hex'),0,1,100,10)"
negative_constraint session-state-shape 23514 "trnm_session_families_check1" \
  "INSERT INTO trnm_session_families VALUES (decode(repeat('6a',16),'hex'),decode(repeat('7a',16),'hex'),0,NULL,NULL,10,10)"
negative_constraint refresh-consumed-shape 23514 "trnm_refresh_tokens_check" \
  "INSERT INTO trnm_refresh_tokens VALUES (decode(repeat('66',16),'hex'),decode(repeat('8a',16),'hex'),decode(repeat('8b',32),'hex'),1,1,10,NULL)"
negative_constraint storage-collection 23514 "trnm_storage_objects_collection_check" \
  "INSERT INTO trnm_storage_objects (collection, object_key, user_id, value_bytes, version_digest, read_permission, write_permission, updated_at_ms) VALUES ('','bad',decode(repeat('7a',16),'hex'),decode('01','hex'),decode(repeat('9a',32),'hex'),2,1,10)"

psql_file trnm_source "$ROOT/scripts/postgresql-semantic-snapshot.sql" \
  > "$EVIDENCE_DIR/source-data.txt"
psql_file trnm_source "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
  > "$EVIDENCE_DIR/source-catalog.txt"
docker exec "$CONTAINER" pg_dump -U trnm --format=custom --no-owner --no-acl trnm_source \
  > "$EVIDENCE_DIR/source.dump"
test -s "$EVIDENCE_DIR/source.dump"
psql_command postgres "CREATE DATABASE trnm_restored" >/dev/null
docker exec -i "$CONTAINER" pg_restore -U trnm --exit-on-error --no-owner --no-acl \
  -d trnm_restored < "$EVIDENCE_DIR/source.dump"
psql_file trnm_restored "$ROOT/scripts/postgresql-semantic-snapshot.sql" \
  > "$EVIDENCE_DIR/restored-data.txt"
psql_file trnm_restored "$ROOT/scripts/postgresql-catalog-snapshot.sql" \
  > "$EVIDENCE_DIR/restored-catalog.txt"
cmp --silent "$EVIDENCE_DIR/source-data.txt" "$EVIDENCE_DIR/restored-data.txt"
cmp --silent "$EVIDENCE_DIR/source-catalog.txt" "$EVIDENCE_DIR/restored-catalog.txt"

docker inspect --format='{{.Image}}' "$CONTAINER" > "$EVIDENCE_DIR/image-id.txt"
python3 - "$ROOT" "$EVIDENCE_DIR" "$POSTGRES_IMAGE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root=Path(sys.argv[1])
evidence=Path(sys.argv[2])
image=sys.argv[3]
def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
manifest={
    "schema":"trillionnium.postgresql-semantic-recovery-evidence.v1",
    "profile":"postgresql",
    "image_reference":image,
    "image_id":(evidence/"image-id.txt").read_text().strip(),
    "migration_lock_sha256":digest(root/"migrations/MIGRATION_CHAIN.lock.json"),
  "schema_identity":json.loads((evidence/"schema-identity.json").read_text()),
  "migration_chain_validation":json.loads((evidence/"migration-chain-validation.json").read_text()),
    "backup_sha256":digest(evidence/"source.dump"),
    "source_data_sha256":digest(evidence/"source-data.txt"),
    "restored_data_sha256":digest(evidence/"restored-data.txt"),
    "source_catalog_sha256":digest(evidence/"source-catalog.txt"),
    "restored_catalog_sha256":digest(evidence/"restored-catalog.txt"),
    "semantic_data_equal": True,
    "semantic_catalog_equal": True,
    "repeat_migration_rejected_without_catalog_change": True,
    "negative_constraint_probe_count": 10,
    "claim_boundary": {
        "accepted_evidence": False,
        "independently_accepted": False,
        "pitr_proven": False,
        "approved_rpo_rto": False,
        "ha_proven": False,
        "production_ready": False,
    },
}
if manifest["source_data_sha256"] != manifest["restored_data_sha256"]:
    raise SystemExit("semantic data digest mismatch")
if manifest["source_catalog_sha256"] != manifest["restored_catalog_sha256"]:
    raise SystemExit("semantic catalog digest mismatch")
(evidence/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
print(json.dumps(manifest,sort_keys=True))
PY
python3 "$ROOT/scripts/check-postgresql-semantic-recovery.py"
