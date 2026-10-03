#!/usr/bin/env bash
set -Eeuo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *) echo "usage: $0 postgresql|cockroachdb" >&2; exit 64 ;;
esac

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

image_for() {
  python3 - "$profile" <<'PYIMAGE'
import json,re,sys
from pathlib import Path
profile=sys.argv[1]
image=json.loads(Path("config/database-test-images.json").read_text())["profiles"][profile]["image"]
pattern={"postgresql":r"postgres@sha256:[0-9a-f]{64}","cockroachdb":r"cockroachdb/cockroach@sha256:[0-9a-f]{64}"}[profile]
if re.fullmatch(pattern,image) is None:
    raise SystemExit("current database image config must have a complete immutable digest")
print(image)
PYIMAGE
}

image=$(image_for)
case "$profile" in
  postgresql) requested_image=${TRNM_POSTGRES_IMAGE:-$image} ;;
  cockroachdb) requested_image=${TRNM_COCKROACH_IMAGE:-$image} ;;
esac
if [[ "$requested_image" != "$image" ]]; then
  echo 'database image override does not match config/database-test-images.json' >&2
  exit 64
fi
commit=$(git rev-parse HEAD)
run_id=${TRNM_RUN_ID:-local}
evidence_root=${TRNM_EVIDENCE_ROOT:-run/outbox-final-attempt-reaper}
evidence="$evidence_root/$profile"
rm -rf "$evidence"
mkdir -p "$evidence/logs"
exec 3>&1 4>&2
exec > >(tee "$evidence/logs/run.log" >&3) 2>&1
tee_pid=$!

container="trnm-outbox-final-attempt-${profile}-${run_id//[^a-zA-Z0-9_.-]/-}"
database_url=
cleanup() {
  if [[ -n "${container:-}" ]]; then
    docker rm -f "$container" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'status=$?; printf "status=failed\nexit_code=%s\n" "$status" >"$evidence/result.env"; exit "$status"' ERR

printf 'repository=TrillionniumFoundation/TrillionniumGame\ncommit=%s\nprofile=%s\nimage=%s\nrun_id=%s\nmigration_lock=migrations/MIGRATION_CHAIN.lock.json\n' \
  "$commit" "$profile" "$image" "$run_id" >"$evidence/identity.env"
docker pull "$image" 2>&1 | tee "$evidence/logs/docker-pull.log"
docker image inspect "$image" >"$evidence/image-inspect.json"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
image_repo_digests=$(docker image inspect --format '{{json .RepoDigests}}' "$image")
printf 'image_id=%s\nrepo_digests=%s\n' "$image_id" "$image_repo_digests" >"$evidence/image.txt"
printf 'image_id=%s\n' "$image_id" >>"$evidence/identity.env"

verify_running_image() {
  local profile=$1 container=$2 expected_id=$3 evidence=$4
  actual_image_id=$(docker inspect --format '{{.Image}}' "$container") || return
  [[ "$actual_image_id" == "$expected_id" ]] || return 1
  printf 'container_image_id=%s\n' "$actual_image_id" >"$evidence/container-image.txt"
  case "$profile" in
    postgresql) docker exec "$container" postgres --version >"$evidence/database-version.txt" || return ;;
    cockroachdb) docker exec "$container" /cockroach/cockroach version >"$evidence/database-version.txt" || return ;;
    *) return 64 ;;
  esac
  python3 - "$profile" "$evidence/database-version.txt" <<'PYVERSION'
import json,sys
from pathlib import Path
expected=json.loads(Path("config/database-test-images.json").read_text())["profiles"][sys.argv[1]]["version_output"]
actual=Path(sys.argv[2]).read_text()
if actual.strip() != expected.strip():
    raise SystemExit("running database binary version does not match current pinned profile")
PYVERSION
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]]
}

if [[ "$profile" == postgresql ]]; then
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
  sql_exec() {
    docker exec -e PGPASSWORD=postgres "$container" psql -At \
      -h 127.0.0.1 -U postgres -d trnm -v ON_ERROR_STOP=1 -c "$1"
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
  sql_exec() {
    docker exec "$container" cockroach sql --insecure --format=tsv \
      --host=127.0.0.1:26257 --database=trnm --execute="$1" | tail -n +2
  }
fi

verify_running_image "$profile" "$container" "$image_id" "$evidence"

python3 scripts/check-migration-lock.py >"$evidence/migration-chain-validation.json"
cp migrations/MIGRATION_CHAIN.lock.json "$evidence/migration-chain.lock.json"
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
TRNM_SCHEMA_SOURCE_COMMIT="$commit" \
TRNM_SCHEMA_APPLIED_AT_MS=1 \
  bash scripts/apply-authoritative-schema.sh migrate \
  >"$evidence/schema-identity.json" 2>"$evidence/schema-migration.log"
python3 scripts/check-authoritative-schema-identity.py "$evidence/schema-identity.json" "$profile" \
  --mode fresh --source-commit="$commit" >"$evidence/schema-identity-check.json"
python3 scripts/capture-schema-source-selection.py --root "$evidence" --profile "$profile" \
  --commit "$commit" --tree "$(git rev-parse HEAD^{tree})" >"$evidence/schema-source-capture.json"
python3 - "$evidence/schema-identity.json" >>"$evidence/identity.env" <<'PYIDENTITY'
import json,sys
from pathlib import Path
identity=json.loads(Path(sys.argv[1]).read_text())
for key in ["schema_version","chain_digest","digest_algorithm","storage_writer_epoch"]:
    print(f"{key}={identity[key]}")
PYIDENTITY

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
dead_reason_hex=c57f69b9f67ddf67d5d6a49b4527af2e17ad313bfbc2f8397b5f9120541be25a

run_scenario() {
  local boundary=$1 entity_byte=$2 command_byte=$3 fingerprint_byte=$4
  local bootstrap_state_byte=$5 next_state_byte=$6 committed_at_ms=$7
  local expected_exit=$8 expected_dead_letter_count=$9
  local scenario="$evidence/$boundary" spool="$evidence/$boundary/spool"
  local intent_byte=$((command_byte + 2)) intent_hex spool_path crash_status
  local outbox_row_count terminal_dead_letter_count
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

  # Dead-lettering is an in-place terminal transition on trnm_outbox. Assert
  # the one canonical row, complete fencing metadata and exact stable reason;
  # no shadow/dead-letter side table exists in the authoritative schema.
  outbox_row_count=$(sql_exec \
    "SELECT COUNT(*) FROM trnm_outbox WHERE intent_id = decode('$intent_hex','hex');")
  terminal_dead_letter_count=$(sql_exec \
    "SELECT COUNT(*) FROM trnm_outbox WHERE intent_id = decode('$intent_hex','hex') \
     AND state = 3 AND attempt = 1 AND lease_generation = 1 \
     AND owner_node IS NULL AND receipt_digest IS NULL \
     AND dead_reason_digest = decode('$dead_reason_hex','hex');")
  test "$outbox_row_count" = "$expected_dead_letter_count"
  test "$terminal_dead_letter_count" = "$expected_dead_letter_count"

  case "$boundary" in
    crash-before-publish)
      test ! -e "$spool_path"
      printf 'possible_lost_effect_declared=true\nspool_effect_count=0\noutbox_row_count=%s\ndead_letter_count=%s\n' \
        "$outbox_row_count" "$terminal_dead_letter_count" >"$scenario/result.env"
      ;;
    crash-after-publish)
      test -f "$spool_path"
      sha256sum -c "$scenario/spool-before.sha256"
      spool_count=$(find "$spool" -maxdepth 1 -type f -name '*.json' | wc -l)
      test "$spool_count" -eq 1
      printf 'possible_lost_effect_declared=false\nspool_effect_count=1\noutbox_row_count=%s\ndead_letter_count=%s\n' \
        "$outbox_row_count" "$terminal_dead_letter_count" >"$scenario/result.env"
      ;;
  esac
}

run_scenario crash-before-publish 20 21 22 23 24 3100 71 1
run_scenario crash-after-publish 30 31 32 33 34 4100 70 1

printf 'status=passed\nprofile=%s\ncommit=%s\n' "$profile" "$commit" >"$evidence/result.env"
cat "$evidence/result.env"

# Stop the process-substitution logger before calculating retained-member
# digests. Otherwise run.log can grow after it is hashed, and redirecting the
# manifest creates files.sha256 early enough for it to hash itself.
exec 1>&3 2>&4
exec 3>&- 4>&-
wait "$tee_pid"
(
  cd "$evidence"
  find . -type f ! -path './files.sha256' -print0 \
    | LC_ALL=C sort -z | xargs -0 sha256sum >files.sha256
  sha256sum --check files.sha256
)
