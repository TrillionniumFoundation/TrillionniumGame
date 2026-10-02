#!/usr/bin/env bash
set -euo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *) echo "usage: $0 <postgresql|cockroachdb>" >&2; exit 64 ;;
esac

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
candidate_head=$(git rev-parse --verify HEAD)
candidate_tree=$(git rev-parse --verify 'HEAD^{tree}')
if [[ "${CANDIDATE_SHA:-$candidate_head}" != "$candidate_head" ]]; then
  echo 'backup candidate HEAD does not match the requested source' >&2
  exit 1
fi

for command in docker cargo python3 sha256sum cmp; do
  command -v "$command" >/dev/null || {
    echo "missing required command: $command" >&2
    exit 69
  }
done

database_image() {
  python3 - "$1" <<'PY_IMAGE'
import json,re,sys
from pathlib import Path
image=json.loads(Path('config/database-test-images.json').read_text())['profiles'][sys.argv[1]]['image']
assert re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}',image)
print(image)
PY_IMAGE
}
postgres_image=$(database_image postgresql)
cockroach_image=$(database_image cockroachdb)

prepare_pinned_image() {
  local image=$1
  docker image inspect "$image" > "$evidence/image-inspect.json"
  image_id=$(docker image inspect --format '{{.Id}}' "$image")
  printf '%s\n' "$image_id" > "$evidence/image-id.txt"
  docker image inspect --format '{{json .RepoDigests}}' "$image" > "$evidence/repo-digests.json"
  python3 - "$image" "$evidence/repo-digests.json" <<'PY_DIGEST'
import json,sys
from pathlib import Path
if sys.argv[1] not in json.loads(Path(sys.argv[2]).read_text()):
    raise SystemExit("pulled image RepoDigests does not contain the current pinned reference")
PY_DIGEST
}
verify_running_image() {
  local actual_image_id
  actual_image_id=$(docker inspect --format '{{.Image}}' "$container")
  [[ "$actual_image_id" == "$image_id" ]]
  printf '%s\n' "$actual_image_id" > "$evidence/container-image.txt"
  case "$profile" in
    postgresql) docker exec "$container" postgres --version > "$evidence/database-version.txt" ;;
    cockroachdb) docker exec "$container" /cockroach/cockroach version > "$evidence/database-version.txt" ;;
  esac
  python3 - "$profile" "$evidence/database-version.txt" <<'PY_VERSION'
import json,sys
from pathlib import Path
expected=json.loads(Path('config/database-test-images.json').read_text())['profiles'][sys.argv[1]]['version_output']
if Path(sys.argv[2]).read_text().strip() != expected.strip():
    raise SystemExit("running database version does not match current pinned profile")
PY_VERSION
}

evidence_root=${TRNM_EVIDENCE_ROOT:-run/pgwire-backup-restore}
evidence="$evidence_root/$profile"
rm -rf "$evidence"
mkdir -p "$evidence"
container="trnm-restore-${profile}-${TRNM_RUN_ID:-$$}"
stage=initialize
diagnostic_logs=()

begin_stage() {
  stage=$1
  shift
  diagnostic_logs=("$@")
  printf 'backup/restore stage: profile=%s stage=%s\n' "$profile" "$stage"
}

restored_database_url() {
  python3 - "$1" <<'PY_RESTORED_URL'
import sys
from urllib.parse import urlsplit, urlunsplit
try:
    source = urlsplit(sys.argv[1])
    if source.scheme not in ('postgres', 'postgresql') or not source.netloc or source.path != '/trnm':
        raise ValueError()
except ValueError:
    raise SystemExit('invalid backup source database URL') from None
print(urlunsplit(source._replace(path='/trnm_restore')))
PY_RESTORED_URL
}

report_failure() {
  local status=$1
  printf 'backup/restore failed: profile=%s stage=%s exit=%s\n' "$profile" "$stage" "$status" >&2
  python3 - "${diagnostic_logs[@]}" <<'PY_FAILURE_LOGS'
import re, sys
from pathlib import Path
for name in sys.argv[1:]:
    path = Path(name)
    if not path.is_file():
        continue
    with path.open('rb') as source:
        source.seek(0, 2)
        offset = max(0, source.tell() - 65536)
        source.seek(offset)
        data = source.read(65536)
    if offset:
        # A partial first line could begin inside a credential-bearing URL.
        data = data.partition(b'\n')[2]
    text = data.decode('utf-8', errors='replace')
    # Discard the remainder of a URL-bearing line as userinfo can contain
    # punctuation that is indistinguishable from surrounding log delimiters.
    text = re.sub(r'(?i)\bpostgres(?:ql)?://[^\r\n]*', '<redacted-database-url>', text)
    text = re.sub(r'''(?i)\b(password|postgres_password|pgpassword)(\s*[=:]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''',
                  r'\1\2<redacted>', text)
    print(f'--- {path.name} (last 80 lines, at most 64 KiB) ---', file=sys.stderr)
    print('\n'.join(text.splitlines()[-80:]), file=sys.stderr)
PY_FAILURE_LOGS
}

cleanup() {
  status=$?
  if (( status != 0 )); then
    report_failure "$status" || true
  fi
  if [[ ! -f "$evidence/container.log" ]]; then
    docker logs "$container" > "$evidence/container.log" 2>&1 || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

seal_backup_evidence() {
  python3 - "$root" "$evidence" "$profile" "$candidate_head" "$candidate_tree" <<'PY_BACKUP_SEAL'
import hashlib, importlib.util, json, os, re, shutil, subprocess, sys
from pathlib import Path

source, retained = Path(sys.argv[1]), Path(sys.argv[2])
profile, commit, tree = sys.argv[3:]

def require(condition, reason):
    if not condition:
        raise SystemExit(reason)

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, source / relative)
    require(spec is not None and spec.loader is not None, 'backup source checker unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

migrations = load('backup_migrations', 'scripts/check-migration-lock.py')
schemas = load('backup_schemas', 'scripts/check-authoritative-schema-identity.py')
sealer = load('backup_shared_sealer', 'scripts/seal-outbox-final-attempt.py')
sealer.retained_files(retained)
git = lambda *args: subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
require(git('rev-parse', '--verify', 'HEAD') == commit and git('rev-parse', '--verify', 'HEAD^{tree}') == tree,
        'backup source identity changed during execution')
require(os.environ.get('CANDIDATE_SHA', commit) == commit, 'backup candidate commit mismatch')
require(re.fullmatch('[0-9a-f]{40}', commit) and re.fullmatch('[0-9a-f]{40}', tree), 'invalid backup source identity')
repository = os.environ.get('CANDIDATE_REPOSITORY', '')
workflow_repository = os.environ.get('GITHUB_REPOSITORY', '')
for value in (repository, workflow_repository):
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value), 'backup repository context is missing or invalid')
run = os.environ.get('GITHUB_RUN_ID', '')
attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
require(re.fullmatch(r'[1-9][0-9]{0,18}', run) and re.fullmatch(r'[1-9][0-9]{0,18}', attempt),
        'backup run context is missing or invalid')
workflow = '.github/workflows/database-backup-restore.yml'
workflow_ref = os.environ.get('GITHUB_WORKFLOW_REF', '')
workflow_sha = os.environ.get('GITHUB_WORKFLOW_SHA', '')
require(workflow_ref.startswith(workflow_repository + '/' + workflow + '@') and len(workflow_ref) <= 1024
        and '\n' not in workflow_ref and '\r' not in workflow_ref, 'backup workflow context is missing or invalid')
require(re.fullmatch('[0-9a-f]{40}', workflow_sha), 'backup workflow commit context is missing or invalid')
require(os.environ.get('GITHUB_JOB') == 'restore', 'backup job context is missing or invalid')

validation = migrations.validate(source)
lock = migrations.load_lock(source)
lock_bytes = (source / 'migrations/MIGRATION_CHAIN.lock.json').read_bytes()
require(migrations.git_blob_sha1(lock_bytes) == git('rev-parse', 'HEAD:migrations/MIGRATION_CHAIN.lock.json'),
        'backup migration lock differs from candidate commit')
require((retained / 'migration-lock.json').read_bytes() == lock_bytes, 'retained backup migration lock differs')
require(schemas.decode_identity_document((retained / 'migration-chain-validation.json').read_bytes()) == validation,
        'retained backup chain validation differs')
chains, version, table_count = schemas.validated_source()
fresh = schemas.decode_identity_document((retained / 'schema-identity.json').read_bytes())
restored = schemas.decode_identity_document((retained / 'restored-schema-identity.json').read_bytes())
schemas.validate_identity(fresh, profile=profile, chains=chains, schema_version=version,
                          table_count=table_count, mode='fresh', source_commit=commit)
schemas.validate_identity(restored, profile=profile, chains=chains, schema_version=version,
                          table_count=table_count, mode='verify')
for field in ('source_commit', 'upgrade_source_commit', 'v2_apply_source_commit', 'v3_apply_source_commit'):
    require(restored[field] == fresh[field], 'restored backup schema provenance differs')
check = {'schema': 'trillionnium.authoritative-schema-identity-check.v1', 'profile': profile,
         'schema_version': version, 'chain_digest': fresh['chain_digest'],
         'identity_verified': True, 'compatibility_credit': False}
for name in ('schema-identity-check.json', 'restored-schema-identity-check.json'):
    require(schemas.decode_identity_document((retained / name).read_bytes()) == check,
            'retained backup schema check differs')

ordered = lock['profiles'][profile]['ordered_files']
require(len(ordered) == 4 and fresh['schema_version'] == 4 and fresh['storage_writer_epoch'] == 4,
        'backup schema v3 complete-chain ABI differs')
projection = load('backup_projection', 'scripts/check-pgwire-backup-restore.py')
source_snapshot = projection.validate_storage_snapshot_bytes(
    (retained / 'source-storage-v3.txt').read_bytes(), profile, fresh, 'restore')
restored_snapshot = projection.validate_storage_snapshot_bytes(
    (retained / 'restored-storage-v3.txt').read_bytes(), profile, restored, 'restore')
require(source_snapshot == restored_snapshot, 'restored storage v3 witness summary differs')
(retained / 'storage-v3-snapshot-check.json').write_text(json.dumps(source_snapshot, sort_keys=True) + '\n')
for item in ordered:
    path = item['path']
    require(not (source / path).is_symlink(), 'backup migration source symlink is forbidden')
    require(item['git_blob_sha1'] == git('rev-parse', 'HEAD:' + path), 'backup migration differs from candidate commit')
    destination = retained / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / path, destination)
sql_files = {path.relative_to(retained).as_posix(): path.read_bytes()
             for path in sealer.retained_files(retained) if path.relative_to(retained).as_posix().startswith('migrations/')}
sealer.VERIFIER.verify_migration_files(sql_files, ordered)
identity = {
    'schema': 'trillionnium.backup-restore-identity.v1', 'repository': repository,
    'commit': commit, 'tree': tree, 'profile': profile, 'run_id': run, 'run_attempt': attempt,
    'workflow_repository': workflow_repository, 'workflow': workflow, 'workflow_ref': workflow_ref,
    'workflow_sha': workflow_sha, 'job': 'restore', 'job_name': 'backup-restore-' + profile,
    'job_identity_kind': 'workflow_job_key_and_matrix_profile',
    'schema_version': fresh['schema_version'], 'storage_writer_epoch': fresh['storage_writer_epoch'],
    'chain_digest': fresh['chain_digest'], 'digest_algorithm': fresh['digest_algorithm'],
    'source_commit': fresh['source_commit'], 'upgrade_source_commit': fresh['upgrade_source_commit'], 'v2_apply_source_commit': fresh['v2_apply_source_commit'], 'v3_apply_source_commit': fresh['v3_apply_source_commit'],
    'compatibility_credit': False, 'accepted_evidence': False, 'production_ready': False,
}
(retained / 'identity.json').write_text(json.dumps(identity, sort_keys=True, separators=(',', ':')) + '\n')
manifest = retained / 'SHA256SUMS'
manifest.unlink(missing_ok=True)
files = []
for path in sealer.retained_files(retained):
    relative = path.relative_to(retained).as_posix()
    require('\n' not in relative and '\r' not in relative and '\\' not in relative,
            'backup evidence path cannot be checksummed safely')
    files.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{relative}')
require(files, 'backup evidence inventory is empty')
document = '\n'.join(sorted(files, key=lambda line: line[66:])) + '\n'
manifest.write_text(document)
sealer.retained_files(retained)
PY_BACKUP_SEAL
  (cd "$evidence" && sha256sum --check --strict SHA256SUMS)
}

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
  trnm_storage_import_jobs
  trnm_storage_import_pages
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
  singleton
  'manifest_digest,page_index'
)

seed_rust_contracts() {
  local database_url=$1
  begin_stage seed-fault "$evidence/seed-fault.log"
  TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" \
    cargo test -p trnm-persistence-pg --test fault_matrix --locked -- --nocapture \
    2>&1 | tee "$evidence/seed-fault.log"
  begin_stage seed-recovery "$evidence/seed-recovery.log"
  TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" \
    TRNM_RECOVERY_PHASE=seed \
    cargo test -p trnm-persistence-pg --test recovery --locked -- --nocapture \
    2>&1 | tee "$evidence/seed-recovery.log"
}

if [[ "$profile" == postgresql ]]; then
  port=${TRNM_POSTGRES_PORT:-55434}
  database_url="postgres://trnm:trnm-pass@127.0.0.1:${port}/trnm"
  begin_stage pull-image "$evidence/image-pull.log"
  docker pull "$postgres_image" | tee "$evidence/image-pull.log"
  begin_stage inspect-image "$evidence/image-inspect.json"
  prepare_pinned_image "$postgres_image"
  begin_stage start-database "$evidence/container-id.txt"
  docker run -d --name "$container" -p "${port}:5432" \
    -e POSTGRES_USER=trnm \
    -e POSTGRES_PASSWORD=trnm-pass \
    -e POSTGRES_DB=trnm \
    "$postgres_image" > "$evidence/container-id.txt"

  begin_stage database-ready
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

  begin_stage verify-image "$evidence/database-version.txt"
  verify_running_image
  begin_stage migrate-schema "$evidence/migration.log"
  TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" TRNM_SCHEMA_SOURCE_COMMIT="$candidate_head" \
    bash scripts/apply-authoritative-schema.sh migrate \
    > "$evidence/schema-identity.json" 2> "$evidence/migration.log"
  begin_stage check-source-schema "$evidence/schema-identity-check.json"
  python3 scripts/check-authoritative-schema-identity.py "$evidence/schema-identity.json" "$profile" --mode fresh --source-commit "$candidate_head" \
    > "$evidence/schema-identity-check.json"
  seed_rust_contracts "$database_url"

  begin_stage seed-domain "$evidence/domain-seed.log"
  docker exec "$container" psql -v ON_ERROR_STOP=1 -U trnm -d trnm -c "
    INSERT INTO trnm_session_families VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a2',16),'hex'),0,
       decode(repeat('a3',16),'hex'),NULL,10,10);
    INSERT INTO trnm_refresh_tokens VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a3',16),'hex'),
       decode(repeat('a4',32),'hex'),0,0,10,NULL);
    -- Known raw request and native JSONB text have independent fingerprints.
    WITH input AS (SELECT decode('207b202262223a20322c202261223a20312e3030207d20','hex') AS request),
         projected AS (SELECT request, convert_from(request,'UTF8') AS request_text,
                       (convert_from(request,'UTF8'))::JSONB AS native_value FROM input)
    INSERT INTO trnm_storage_objects (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms)
    SELECT 'restore', 'fixture', decode(repeat('a5',16),'hex'), request, sha256(request),
           native_value, md5(request_text), sha256(convert_to(native_value::TEXT,'UTF8')),
           'write-request-bytes', NULL, 2, 1, 10 FROM projected;
    -- A synthetic storage fixture manifest marks unknown history, never an invented request.
    INSERT INTO trnm_storage_objects (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms) VALUES
      ('restore', 'unknown-empty', decode(repeat('a5',16),'hex'), NULL, NULL,
       'null'::JSONB, '', sha256(convert_to(('null'::JSONB)::TEXT,'UTF8')), 'nakama-export-unknown-request',
       sha256(convert_to('synthetic storage fixture manifest','UTF8')), 2, 1, 10),
      ('restore', 'unknown-unicode', decode(repeat('a5',16),'hex'), NULL, NULL,
       '[3, true, null]'::JSONB, '版本A*', sha256(convert_to(('[3, true, null]'::JSONB)::TEXT,'UTF8')),
       'nakama-export-unknown-request', sha256(convert_to('synthetic storage fixture manifest','UTF8')), 2, 1, 10);
    INSERT INTO trnm_storage_objects
      (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms, create_time, update_time)
      SELECT collection, 'known-time', user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms,
        '1969-12-31 23:59:59.999999+00'::TIMESTAMPTZ, '2024-02-29 00:00:00.123456+00'::TIMESTAMPTZ
      FROM trnm_storage_objects WHERE collection='restore' AND object_key='fixture';
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
        -c "SET TIME ZONE 'UTC'; SELECT * FROM ${tables[$index]} ORDER BY ${orders[$index]}" \
        >> "$output"
    done
  }

  begin_stage source-snapshot
  snapshot trnm "$evidence/source.csv"
  docker exec -i "$container" psql -X -q -v ON_ERROR_STOP=1 -U trnm -d trnm \
    < scripts/postgresql-semantic-snapshot.sql > "$evidence/source-storage-v3.txt"
  begin_stage backup
  docker exec "$container" pg_dump -Fc --no-owner --no-privileges \
    -U trnm -d trnm > "$evidence/backup.dump"
  test -s "$evidence/backup.dump"
  begin_stage create-restore-database
  docker exec "$container" createdb -U trnm trnm_restore
  begin_stage restore "$evidence/restore.log"
  docker exec -i "$container" pg_restore --no-owner --no-privileges \
    -U trnm -d trnm_restore < "$evidence/backup.dump" \
    > "$evidence/restore.log" 2>&1
  begin_stage restored-database-url
  restored_url=$(restored_database_url "$database_url")
  begin_stage verify-restored-schema "$evidence/restored-schema-build.log"
  TRNM_DATABASE_URL="$restored_url" TRNM_DATABASE_PROFILE="$profile" \
    bash scripts/apply-authoritative-schema.sh verify \
    > "$evidence/restored-schema-identity.json" 2> "$evidence/restored-schema-build.log"
  begin_stage check-restored-schema "$evidence/restored-schema-identity-check.json"
  python3 scripts/check-authoritative-schema-identity.py "$evidence/restored-schema-identity.json" "$profile" --mode verify \
    > "$evidence/restored-schema-identity-check.json"
  begin_stage restored-snapshot
  snapshot trnm_restore "$evidence/restored.csv"
  docker exec -i "$container" psql -X -q -v ON_ERROR_STOP=1 -U trnm -d trnm_restore \
    < scripts/postgresql-semantic-snapshot.sql > "$evidence/restored-storage-v3.txt"
else
  database_url='postgres://root@127.0.0.1:26257/trnm?sslmode=disable'
  begin_stage pull-image "$evidence/image-pull.log"
  docker pull "$cockroach_image" | tee "$evidence/image-pull.log"
  begin_stage inspect-image "$evidence/image-inspect.json"
  prepare_pinned_image "$cockroach_image"
  begin_stage start-database "$evidence/container-id.txt"
  docker run -d --name "$container" --network host \
    "$cockroach_image" start-single-node --insecure \
    --store=/cockroach/cockroach-data \
    --external-io-dir=/cockroach/cockroach-data/extern \
    --listen-addr=127.0.0.1:26257 \
    --http-addr=127.0.0.1:8080 \
    > "$evidence/container-id.txt"

  begin_stage database-ready
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

  begin_stage create-source-database
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --execute='CREATE DATABASE trnm' >/dev/null
  begin_stage verify-image "$evidence/database-version.txt"
  verify_running_image
  begin_stage migrate-schema "$evidence/migration.log"
  TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" TRNM_SCHEMA_SOURCE_COMMIT="$candidate_head" \
    bash scripts/apply-authoritative-schema.sh migrate \
    > "$evidence/schema-identity.json" 2> "$evidence/migration.log"
  begin_stage check-source-schema "$evidence/schema-identity-check.json"
  python3 scripts/check-authoritative-schema-identity.py "$evidence/schema-identity.json" "$profile" --mode fresh --source-commit "$candidate_head" \
    > "$evidence/schema-identity-check.json"
  seed_rust_contracts "$database_url"

  begin_stage seed-domain "$evidence/domain-seed.log"
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=trnm --set=errexit=true --execute="
    INSERT INTO trnm_session_families VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a2',16),'hex'),0,
       decode(repeat('a3',16),'hex'),NULL,10,10);
    INSERT INTO trnm_refresh_tokens VALUES
      (decode(repeat('a1',16),'hex'),decode(repeat('a3',16),'hex'),
       decode(repeat('a4',32),'hex'),0,0,10,NULL);
    -- Known raw request and native JSONB text have independent fingerprints.
    WITH input AS (SELECT decode('207b202262223a20322c202261223a20312e3030207d20','hex') AS request),
         projected AS (SELECT request, pg_catalog.convert_from(request,'UTF8') AS request_text,
                       (pg_catalog.convert_from(request,'UTF8'))::JSONB AS native_value FROM input)
    INSERT INTO trnm_storage_objects (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms)
    SELECT 'restore', 'fixture', decode(repeat('a5',16),'hex'), request, decode(sha256(request),'hex'),
           native_value, md5(request_text), decode(sha256(native_value::TEXT),'hex'),
           'write-request-bytes', NULL, 2, 1, 10 FROM projected;
    -- A synthetic storage fixture manifest marks unknown history, never an invented request.
    INSERT INTO trnm_storage_objects (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms) VALUES
      ('restore', 'unknown-empty', decode(repeat('a5',16),'hex'), NULL, NULL,
       'null'::JSONB, '', decode(sha256(('null'::JSONB)::TEXT),'hex'), 'nakama-export-unknown-request',
       decode(sha256('synthetic storage fixture manifest'),'hex'), 2, 1, 10),
      ('restore', 'unknown-unicode', decode(repeat('a5',16),'hex'), NULL, NULL,
       '[3, true, null]'::JSONB, '版本A*', decode(sha256(('[3, true, null]'::JSONB)::TEXT),'hex'),
       'nakama-export-unknown-request', decode(sha256('synthetic storage fixture manifest'),'hex'), 2, 1, 10);
    INSERT INTO trnm_storage_objects
      (collection, object_key, user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms, create_time, update_time)
      SELECT collection, 'known-time', user_id, value_bytes, version_digest, value_jsonb, public_version, value_projection_digest, value_origin, source_manifest_digest, read_permission, write_permission, updated_at_ms,
        '1969-12-31 23:59:59.999999+00'::TIMESTAMPTZ, '2024-02-29 00:00:00.123456+00'::TIMESTAMPTZ
      FROM trnm_storage_objects WHERE collection='restore' AND object_key='fixture';
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

  begin_stage source-snapshot
  snapshot trnm "$evidence/source.csv"
  docker exec -i "$container" /cockroach/cockroach sql --insecure --host=127.0.0.1:26257 \
    --database=trnm --set=errexit=true --format=tsv \
    < scripts/cockroachdb-semantic-snapshot.sql > "$evidence/source-storage-v3.txt"
  begin_stage backup "$evidence/backup.log"
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=defaultdb --set=errexit=true \
    --execute="BACKUP DATABASE trnm INTO 'nodelocal://1/trnm-backup'" \
    > "$evidence/backup.log" 2>&1
  begin_stage backup-manifest
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=defaultdb --format=csv \
    --execute="SHOW BACKUP FROM LATEST IN 'nodelocal://1/trnm-backup'" \
    > "$evidence/backup-manifest.csv"
  test -s "$evidence/backup-manifest.csv"
  begin_stage restore "$evidence/restore.log"
  docker exec "$container" /cockroach/cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=defaultdb --set=errexit=true \
    --execute="RESTORE DATABASE trnm FROM LATEST IN 'nodelocal://1/trnm-backup' \
               WITH new_db_name='trnm_restore'" \
    > "$evidence/restore.log" 2>&1
  begin_stage restored-database-url
  restored_url=$(restored_database_url "$database_url")
  begin_stage verify-restored-schema "$evidence/restored-schema-build.log"
  TRNM_DATABASE_URL="$restored_url" TRNM_DATABASE_PROFILE="$profile" \
    bash scripts/apply-authoritative-schema.sh verify \
    > "$evidence/restored-schema-identity.json" 2> "$evidence/restored-schema-build.log"
  begin_stage check-restored-schema "$evidence/restored-schema-identity-check.json"
  python3 scripts/check-authoritative-schema-identity.py "$evidence/restored-schema-identity.json" "$profile" --mode verify \
    > "$evidence/restored-schema-identity-check.json"
  begin_stage restored-snapshot
  snapshot trnm_restore "$evidence/restored.csv"
  docker exec -i "$container" /cockroach/cockroach sql --insecure --host=127.0.0.1:26257 \
    --database=trnm_restore --set=errexit=true --format=tsv \
    < scripts/cockroachdb-semantic-snapshot.sql > "$evidence/restored-storage-v3.txt"
fi

begin_stage validate-migration-chain "$evidence/migration-chain-validation.json"
cp migrations/MIGRATION_CHAIN.lock.json "$evidence/migration-lock.json"
python3 scripts/check-migration-lock.py > "$evidence/migration-chain-validation.json"
(cd "$evidence" && sha256sum source.csv restored.csv > snapshot-sha256.txt)
begin_stage compare-snapshots
cmp "$evidence/source.csv" "$evidence/restored.csv"
cmp "$evidence/source-storage-v3.txt" "$evidence/restored-storage-v3.txt"
begin_stage seal-evidence
docker inspect "$container" > "$evidence/container-inspect.json"
docker logs "$container" > "$evidence/container.log" 2>&1
cat > "$evidence/summary.json" <<EOF
{"schema":"trillionnium.backup-restore.v1","profile":"$profile","backup_created":true,"empty_restore":true,"semantic_snapshot_equal":true,"production_pitr":false,"multi_node_restore":false,"schema_version":4,"storage_writer_epoch":4,"authoritative_migration_file_count":4,"storage_v3_fixture_count":4}
EOF
seal_backup_evidence
printf 'backup/restore contract passed: profile=%s evidence=%s\n' \
  "$profile" "$evidence"
