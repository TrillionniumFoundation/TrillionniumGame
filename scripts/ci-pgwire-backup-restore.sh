#!/usr/bin/env bash
set -euo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *) echo "usage: $0 <postgresql|cockroachdb>" >&2; exit 64 ;;
esac

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

for command in docker cargo sha256sum cmp; do
  command -v "$command" >/dev/null || {
    echo "missing required command: $command" >&2
    exit 69
  }
done

postgres_image='postgres:17.6-alpine3.22@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94'
cockroach_image='cockroachdb/cockroach:v24.1.2@sha256:105b9d1e10e4845c9c59266bef3c27ff8b82eeaeb1b464c75423408c3a2968ba'
cockroach_image_id='sha256:13156f587d7c94e0d32a3adb9793d06bcbd92a90bfe2b88440d6f74fd6b110ba'

evidence_root=${TRNM_EVIDENCE_ROOT:-run/pgwire-backup-restore}
evidence="$evidence_root/$profile"
rm -rf "$evidence"
mkdir -p "$evidence"
container="trnm-restore-${profile}-${TRNM_RUN_ID:-$$}"

cleanup() {
  status=$?
  docker logs "$container" > "$evidence/container.log" 2>&1 || true
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

tables=(
  trnm_schema_metadata
  trnm_entity_heads
  trnm_command_receipts
  trnm_events
  trnm_outbox
  trnm_command_outbox
  trnm_authority_leases
  trnm_session_families
  trnm_refresh_tokens
  trnm_storage_objects
)
orders=(
  singleton
  entity_id
  'entity_id,command_id'
  'entity_id,sequence'
  intent_id
  'entity_id,command_id,position'
  entity_id
  family_id
  'family_id,token_id'
  'collection,object_key,user_id'
)

seed_rust_contracts() {
  local database_url=$1
  TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" \
    cargo test -p trnm-persistence-pg --test fault_matrix --locked -- --nocapture \
    2>&1 | tee "$evidence/seed-fault.log"
  TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" \
    TRNM_RECOVERY_PHASE=seed \
    cargo test -p trnm-persistence-pg --test recovery --locked -- --nocapture \
    2>&1 | tee "$evidence/seed-recovery.log"
}

if [[ "$profile" == postgresql ]]; then
  port=${TRNM_POSTGRES_PORT:-55434}
  database_url="postgres://trnm:trnm-pass@127.0.0.1:${port}/trnm"
  migration=migrations/postgresql/0001_foundation_up.sql
  docker pull "$postgres_image" | tee "$evidence/image-pull.log"
  docker run -d --name "$container" -p "${port}:5432" \
    -e POSTGRES_USER=trnm \
    -e POSTGRES_PASSWORD=trnm-pass \
    -e POSTGRES_DB=trnm \
    "$postgres_image" > "$evidence/container-id.txt"

  ready=0
  for _ in $(seq 1 150); do
    ready_count=$(docker logs "$container" 2>&1 \
      | grep -c 'database system is ready to accept connections' || true)
    if (( ready_count >= 2 )) \
      && docker exec "$container" psql -At -U trnm -d trnm -c 'SELECT 1' \
        >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  (( ready == 1 )) || {
    docker logs "$container" >&2 || true
    exit 1
  }

  docker exec -i "$container" psql -v ON_ERROR_STOP=1 -U trnm -d trnm \
    < "$migration" > "$evidence/migration.log" 2>&1
  seed_rust_contracts "$database_url"

  docker exec "$container" psql -v ON_ERROR_STOP=1 -U trnm -d trnm -c "
    INSERT INTO trnm_session_families VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a2',16),'hex'),0,
       decode(repeat('a3',16),'hex'),NULL,10,10);
    INSERT INTO trnm_refresh_tokens VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a3',16),'hex'),
       decode(repeat('a4',32),'hex'),0,0,10,NULL);
    INSERT INTO trnm_storage_objects VALUES
      ('restore','fixture',decode(repeat('a5',16),'hex'),decode('010203','hex'),
       decode(repeat('a6',32),'hex'),2,1,10);
    INSERT INTO trnm_authority_leases
      SELECT entity_id,decode(repeat('a7',16),'hex'),1,authority_generation,999999,10
      FROM trnm_entity_heads ORDER BY entity_id LIMIT 1;
  " > "$evidence/domain-seed.log" 2>&1

  snapshot() {
    local database=$1 output=$2
    : > "$output"
    for index in "${!tables[@]}"; do
      printf 'TABLE|%s\n' "${tables[$index]}" >> "$output"
      docker exec "$container" psql -X --csv -U trnm -d "$database" \
        -c "SELECT * FROM ${tables[$index]} ORDER BY ${orders[$index]}" \
        >> "$output"
    done
  }

  snapshot trnm "$evidence/source.csv"
  docker exec "$container" pg_dump -Fc --no-owner --no-privileges \
    -U trnm -d trnm > "$evidence/backup.dump"
  test -s "$evidence/backup.dump"
  docker exec "$container" createdb -U trnm trnm_restore
  docker exec -i "$container" pg_restore --no-owner --no-privileges \
    -U trnm -d trnm_restore < "$evidence/backup.dump" \
    > "$evidence/restore.log" 2>&1
  snapshot trnm_restore "$evidence/restored.csv"
else
  database_url='postgres://root@127.0.0.1:26257/trnm?sslmode=disable'
  migration=migrations/cockroachdb/0001_foundation_up.sql
  docker pull "$cockroach_image" | tee "$evidence/image-pull.log"
  actual_image_id=$(docker image inspect --format '{{.Id}}' "$cockroach_image")
  test "$actual_image_id" = "$cockroach_image_id"
  printf '%s\n' "$actual_image_id" > "$evidence/image-id.txt"
  docker image inspect --format '{{json .RepoDigests}}' "$cockroach_image" \
    > "$evidence/repo-digests.json"
  docker run -d --name "$container" --network host \
    "$cockroach_image" start-single-node --insecure \
    --store=/cockroach/cockroach-data \
    --external-io-dir=/cockroach/cockroach-data/extern \
    --listen-addr=127.0.0.1:26257 \
    --http-addr=127.0.0.1:8080 \
    > "$evidence/container-id.txt"

  stable=0
  for _ in $(seq 1 150); do
    if docker exec "$container" /cockroach/cockroach sql --insecure \
      --host=127.0.0.1:26257 --execute='SELECT 1' >/dev/null 2>&1; then
      stable=$((stable + 1))
      (( stable >= 3 )) && break
    else
      stable=0
    fi
    sleep 1
  done
  (( stable >= 3 )) || {
    docker logs "$container" >&2 || true
    exit 1
  }

  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --execute='CREATE DATABASE trnm' >/dev/null
  docker exec -i "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=trnm --set=errexit=true \
    < "$migration" > "$evidence/migration.log" 2>&1
  seed_rust_contracts "$database_url"

  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=trnm --set=errexit=true --execute="
    INSERT INTO trnm_session_families VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a2',16),'hex'),0,
       decode(repeat('a3',16),'hex'),NULL,10,10);
    INSERT INTO trnm_refresh_tokens VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a3',16),'hex'),
       decode(repeat('a4',32),'hex'),0,0,10,NULL);
    INSERT INTO trnm_storage_objects VALUES
      ('restore','fixture',decode(repeat('a5',16),'hex'),decode('010203','hex'),
       decode(repeat('a6',32),'hex'),2,1,10);
    INSERT INTO trnm_authority_leases
      SELECT entity_id,decode(repeat('a7',16),'hex'),1,authority_generation,999999,10
      FROM trnm_entity_heads ORDER BY entity_id LIMIT 1;
  " > "$evidence/domain-seed.log" 2>&1

  snapshot() {
    local database=$1 output=$2
    : > "$output"
    for index in "${!tables[@]}"; do
      printf 'TABLE|%s\n' "${tables[$index]}" >> "$output"
      docker exec "$container" /cockroach/cockroach sql --insecure \
        --host=127.0.0.1:26257 --database="$database" --format=csv \
        --execute="SELECT * FROM ${tables[$index]} ORDER BY ${orders[$index]}" \
        >> "$output"
    done
  }

  snapshot trnm "$evidence/source.csv"
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=defaultdb --set=errexit=true \
    --execute="BACKUP DATABASE trnm INTO 'nodelocal://1/trnm-backup'" \
    > "$evidence/backup.log" 2>&1
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=defaultdb --format=csv \
    --execute="SHOW BACKUP FROM LATEST IN 'nodelocal://1/trnm-backup'" \
    > "$evidence/backup-manifest.csv"
  test -s "$evidence/backup-manifest.csv"
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=defaultdb --set=errexit=true \
    --execute="RESTORE DATABASE trnm FROM LATEST IN 'nodelocal://1/trnm-backup' \
               WITH new_db_name='trnm_restore'" \
    > "$evidence/restore.log" 2>&1
  snapshot trnm_restore "$evidence/restored.csv"
fi

sha256sum "$evidence/source.csv" "$evidence/restored.csv" \
  > "$evidence/snapshot-sha256.txt"
cmp "$evidence/source.csv" "$evidence/restored.csv"
docker inspect "$container" > "$evidence/container-inspect.json"
cat > "$evidence/summary.json" <<EOF
{"schema":"trillionnium.backup-restore.v1","profile":"$profile","backup_created":true,"empty_restore":true,"semantic_snapshot_equal":true,"production_pitr":false,"multi_node_restore":false}
EOF
find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
printf 'backup/restore contract passed: profile=%s evidence=%s\n' \
  "$profile" "$evidence"
