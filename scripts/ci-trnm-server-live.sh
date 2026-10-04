#!/usr/bin/env bash
set -euo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *) echo 'usage: ci-trnm-server-live.sh postgresql|cockroachdb' >&2; exit 64 ;;
esac

root=$(git rev-parse --show-toplevel)
cd "$root"

candidate_sha=${CANDIDATE_SHA:-$(git rev-parse HEAD)}
test "$(git rev-parse HEAD)" = "$candidate_sha"
test "${#candidate_sha}" -eq 40
test -z "${candidate_sha//[0-9a-f]/}"
candidate_tree=$(git rev-parse HEAD^{tree})
run_id=${TRNM_RUN_ID:-local-$profile}
evidence_root=${TRNM_EVIDENCE_ROOT:-run/server-live}
evidence="$evidence_root/$profile"
rm -rf "$evidence"
mkdir -p "$evidence"
# Cargo runs integration tests from the crate directory. Retain the original
# archive paths while passing absolute directories across that cwd boundary.
evidence_absolute=$(cd "$evidence" && pwd -P)

server_port=${TRNM_SERVER_PORT:-17350}
admin_token='trnm_server_live_admin_token_0123456789abcdef'
container="trnm-server-live-${profile}-${run_id//[^A-Za-z0-9_.-]/-}"
server_pid=''
stage=initialize
diagnostic_logs=()

begin_stage() {
  stage=$1
  shift
  diagnostic_logs=("$@")
  test "${#diagnostic_logs[@]}" -le 2
  printf 'trnm-server live stage: profile=%s stage=%s\n' "$profile" "$stage"
}

report_failure() {
  local status=$1
  TRNM_SERVER_ADMIN_TOKEN="$admin_token" \
  TRNM_SERVER_DATABASE_URL="${database_url:-}" \
    python3 "$root/scripts/print-server-live-failure.py" \
      --profile "$profile" --stage "$stage" --status "$status" -- "${diagnostic_logs[@]}"
}

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

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if (( status != 0 )); then
    report_failure "$status" || true
  fi
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

begin_stage database-start "$evidence/image-pull.log"
docker rm -f "$container" >/dev/null 2>&1 || true

if [[ "$profile" == postgresql ]]; then
  db_port=${TRNM_POSTGRES_PORT:-55435}
  database_url="postgresql://trnm:trnm_live_password@127.0.0.1:${db_port}/trnm"
  docker pull "$postgres_image" | tee "$evidence/image-pull.log"
  prepare_pinned_image "$postgres_image"
  docker run --rm -d \
    --name "$container" \
    -e POSTGRES_DB=trnm \
    -e POSTGRES_USER=trnm \
    -e POSTGRES_PASSWORD=trnm_live_password \
    -p "127.0.0.1:${db_port}:5432" \
    "$postgres_image" > "$evidence/container-id.txt"

  # The official image exposes a temporary Unix-socket initialization server
  # before starting the final TCP postmaster. A single in-container pg_isready
  # can observe that transient process. Require a bounded run of real SQL
  # transactions over 127.0.0.1:5432 while the container remains running.
  python3 scripts/wait-postgres-final-ready.py \
    --container "$container" \
    --user trnm \
    --database trnm \
    --attempts 160 \
    --consecutive-successes 6 \
    --interval-seconds 0.25 \
    > "$evidence/postgres-final-readiness.json"
  python3 -m json.tool "$evidence/postgres-final-readiness.json" >/dev/null
  cat "$evidence/postgres-final-readiness.json"

  db_scalar() {
    docker exec "$container" psql -X -U trnm -d trnm -At -v ON_ERROR_STOP=1 -c "$1"
  }
else
  database_url='postgresql://root@127.0.0.1:26257/trnm?sslmode=disable'
  docker pull "$cockroach_image" | tee "$evidence/image-pull.log"
  prepare_pinned_image "$cockroach_image"
  docker run --rm -d \
    --name "$container" \
    --network host \
    "$cockroach_image" \
    start-single-node \
    --insecure \
    --listen-addr=127.0.0.1:26257 \
    --http-addr=127.0.0.1:18081 \
    --store=/cockroach/cockroach-data > "$evidence/container-id.txt"
  ready=false
  for _ in $(seq 1 160); do
    if docker exec "$container" /cockroach/cockroach sql \
      --insecure --host=127.0.0.1:26257 --execute='SELECT 1' >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 0.25
  done
  if [[ "$ready" != true ]]; then
    docker logs "$container" >&2 || true
    exit 1
  fi
  docker exec "$container" /cockroach/cockroach sql \
    --insecure --host=127.0.0.1:26257 \
    --execute='CREATE DATABASE IF NOT EXISTS trnm'
  db_scalar() {
    docker exec "$container" /cockroach/cockroach sql \
      --insecure --host=127.0.0.1:26257 --database=trnm \
      --format=csv --execute="$1" | tail -n 1
  }
fi

begin_stage database-identity "$evidence/database-version.txt"
verify_running_image
docker inspect "$container" > "$evidence/container-inspect.json"
printf '%s\n' "$candidate_sha" > "$evidence/candidate-commit.txt"
printf '%s\n' "$candidate_tree" > "$evidence/candidate-tree.txt"
printf '%s\n' "$profile" > "$evidence/profile.txt"
printf '%s\n' "$run_id" > "$evidence/run-id.txt"
rustc --version --verbose > "$evidence/rustc-version.txt"
cargo --version --verbose > "$evidence/cargo-version.txt"
docker version > "$evidence/docker-version.txt"
python3 --version > "$evidence/python-version.txt" 2>&1

begin_stage build "$evidence/cargo-build.log"
cargo build --locked -p trnm-persistence-pg --features diagnostic-compat-server --bin trnm-pg-compat-server \
  2>&1 | tee "$evidence/cargo-build.log"
binary=target/debug/trnm-pg-compat-server
test -x "$binary"
sha256sum "$binary" > "$evidence/server-binary-sha256.txt"

export TRNM_SERVER_BIND="127.0.0.1:${server_port}"
export TRNM_SERVER_DATABASE_URL="$database_url"
export TRNM_SERVER_DATABASE_PROFILE="$profile"
export TRNM_SERVER_SCHEMA_SOURCE_COMMIT="$candidate_sha"
export TRNM_SERVER_ADMIN_TOKEN="$admin_token"
export TRNM_SERVER_ALLOW_PLAINTEXT_DATABASE=1
export TRNM_SERVER_MAX_REQUEST_BYTES=131072
export TRNM_SERVER_READ_TIMEOUT_MS=5000
export TRNM_SERVER_WRITE_TIMEOUT_MS=10000

begin_stage check-config "$evidence/check-config.log"
"$binary" check-config > "$evidence/check-config.log" 2>&1
begin_stage check-config-output "$evidence/check-config.log"
grep -qx 'trnm-server configuration valid' "$evidence/check-config.log"
begin_stage credential-check "$evidence/check-config.log"
if grep -Fq 'trnm_live_password' "$evidence/check-config.log"; then
  echo 'database credential leaked by check-config' >&2
  exit 1
fi
if grep -Fq "$admin_token" "$evidence/check-config.log"; then
  echo 'admin token leaked by check-config' >&2
  exit 1
fi
begin_stage migrate "$evidence/check-config.log" "$evidence/migrate.log"
"$binary" migrate > "$evidence/migrate.log" 2>&1
begin_stage migrate-output "$evidence/migrate.log"
grep -qx 'trnm-server migration completed' "$evidence/migrate.log"

# This lane executes the canonical App against the migrated database. The
# existing diagnostic process phases below retain their separate scope.
begin_stage canonical-storage-app "$evidence/canonical-storage-app.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-server --locked --lib \
    runtime::storage_api_tests::canonical_storage_api_live_database \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/canonical-storage-app.log"
canonical_storage_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/canonical-storage-app.log"
)
[[ "$canonical_storage_test_count" =~ ^[0-9]+$ ]]
test "$canonical_storage_test_count" -eq 1
grep -Fxq "canonical_storage_api_live_executed profile=${profile}" \
  "$evidence/canonical-storage-app.log"
grep -Fxq "storage_opaque_conditions_live_executed profile=${profile} write_cases=15 delete_cases=18 batch_cases=2" \
  "$evidence/canonical-storage-app.log"
grep -Fxq "storage_jsonb_v3_live_executed profile=${profile} history_cases=6 opaque_success_cases=4 noop_cases=2 resource_cases=1 native_input_cases=3" \
  "$evidence/canonical-storage-app.log"
test "$(grep -Ec "^storage_jsonb_v3_native_inputs profile=${profile} condition_sql=(accepted|rejected) payload_sql=(accepted|rejected) compatibility_credit=false$" "$evidence/canonical-storage-app.log")" -eq 1
grep -Fxq "storage_homogeneous_app_executed profile=${profile} write_occurrences=13 rollback_cases=3" "$evidence/canonical-storage-app.log"
test "$(grep -Ec '^storage_homogeneous_app_executed ' "$evidence/canonical-storage-app.log")" -eq 1
grep -Fxq "storage_jsonb_native_write_failure_executed profile=${profile} cases=1 fields=15 sqlstate=22P02" "$evidence/canonical-storage-app.log"
test "$(grep -Ec '^storage_jsonb_native_write_failure_executed ' "$evidence/canonical-storage-app.log")" -eq 1
if grep -Fq 'canonical_storage_api_live_skipped' "$evidence/canonical-storage-app.log"; then
  echo 'canonical storage App database lane skipped instead of executing' >&2
  exit 1
fi

# Execute the dedicated client list projection once, without replaying the
# other authority_storage fixtures or their independently owned identities.
begin_stage nakama-client-list-projection "$evidence/nakama-client-list-projection.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test authority_storage \
    nakama_client_listing_modes_cursors_and_integrity_are_database_projected \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/nakama-client-list-projection.log"
nakama_client_list_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/nakama-client-list-projection.log"
)
[[ "$nakama_client_list_test_count" =~ ^[0-9]+$ ]]
test "$nakama_client_list_test_count" -eq 1
grep -Fxq "nakama_client_list_projection_executed profile=${profile}" \
  "$evidence/nakama-client-list-projection.log"
if grep -Fq 'nakama_client_list_projection_skipped' "$evidence/nakama-client-list-projection.log"; then
  echo 'Nakama client list projection database lane skipped instead of executing' >&2
  exit 1
fi

# Execute the actual ACL/OCC precedence and rollback fixture once per profile.
begin_stage storage-occ-precedence "$evidence/storage-occ-precedence.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test authority_storage \
    blind_storage_no_op_preserves_timestamp_after_acl_occ_and_integrity_checks \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/storage-occ-precedence.log"
storage_occ_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/storage-occ-precedence.log"
)
[[ "$storage_occ_test_count" =~ ^[0-9]+$ ]]
test "$storage_occ_test_count" -eq 1
grep -Fxq "storage_blind_write_timestamps_executed profile=${profile}" "$evidence/storage-occ-precedence.log"
if grep -Fq 'storage_blind_write_timestamps_skipped' "$evidence/storage-occ-precedence.log"; then
  echo 'storage OCC precedence database lane skipped instead of executing' >&2
  exit 1
fi

# A required exact storage-clock fixture and an isolated schema lifecycle suite.
begin_stage storage-timestamps "$evidence/storage-timestamps.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test storage_timestamps \
    storage_timestamps_database_clock_no_op_and_atomicity \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/storage-timestamps.log"
storage_timestamps_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/storage-timestamps.log"
)
[[ "$storage_timestamps_test_count" =~ ^[0-9]+$ ]]
test "$storage_timestamps_test_count" -eq 1
grep -Fxq "storage_timestamps_live_executed profile=${profile}" "$evidence/storage-timestamps.log"
if grep -Fq 'storage_timestamps_live_skipped' "$evidence/storage-timestamps.log"; then
  echo 'storage timestamp database lane skipped instead of executing' >&2
  exit 1
fi

# Exercise the persistence JSONB fixture itself, separately from HTTP and
# schema lifecycle fixtures; the marker can only follow this exact live test.
begin_stage storage-native-jsonb "$evidence/storage-native-jsonb.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test storage_jsonb \
    storage_native_jsonb_versions_provenance_and_atomicity \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/storage-native-jsonb.log"
storage_native_jsonb_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/storage-native-jsonb.log"
)
[[ "$storage_native_jsonb_test_count" =~ ^[0-9]+$ ]]
test "$storage_native_jsonb_test_count" -eq 1
grep -Fxq "storage_native_jsonb_live_executed profile=${profile}" "$evidence/storage-native-jsonb.log"
test "$(grep -Fxc "storage_native_jsonb_live_executed profile=${profile}" "$evidence/storage-native-jsonb.log")" -eq 1
if grep -Fq 'storage_native_jsonb_live_skipped' "$evidence/storage-native-jsonb.log"; then
  echo 'storage native JSONB database lane skipped instead of executing' >&2
  exit 1
fi
# Preserve stored raw ACLs and source-key domains through the real repository.
begin_stage storage-v4-acl "$evidence/storage-v4-acl.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test storage_permissions_v4 \
    storage_v4_raw_acl_domains_keys_and_operation_predicates_are_native \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/storage-v4-acl.log"
storage_v4_acl_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/storage-v4-acl.log"
)
[[ "$storage_v4_acl_test_count" =~ ^[0-9]+$ ]]
test "$storage_v4_acl_test_count" -eq 1
grep -Fxq "storage_v4_acl_live_executed profile=${profile}" "$evidence/storage-v4-acl.log"
test "$(grep -Fxc "storage_v4_acl_live_executed profile=${profile}" "$evidence/storage-v4-acl.log")" -eq 1
if grep -Fq 'storage_v4_acl_live_skipped' "$evidence/storage-v4-acl.log"; then
  echo 'storage v4 ACL database lane skipped instead of executing' >&2
  exit 1
fi
# Acquire the closed immutable source annex before any importer fixture write.
begin_stage storage-v4-source-annex "$evidence/storage-v4-source-materialization.json"
python3 scripts/materialize-pinned-storage-upstream.py \
  --destination "$evidence/storage-source-upstream" \
  > "$evidence/storage-v4-source-materialization.json"
begin_stage storage-v4-import-source "$evidence/storage-v4-import-source.json"
python3 scripts/check-trnm-server.py \
  --storage-import-source-archive "$evidence" --profile "$profile" \
  --commit "$candidate_sha" --tree "$candidate_tree" \
  > "$evidence/storage-v4-source-archive.log"
begin_stage storage-v4-import "$evidence/storage-v4-import.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" \
TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY="$evidence_absolute/storage-source-upstream" \
TRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha" \
TRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree" \
TRNM_STORAGE_IMPORT_EVIDENCE_ROOT="$evidence_absolute/storage-v4-import-packets" \
  cargo test -p trnm-persistence-pg --locked --test storage_import_v4 \
    storage_v4_source_export_custody_resume_finish_and_tamper_are_native \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/storage-v4-import.log"
storage_v4_import_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/storage-v4-import.log"
)
[[ "$storage_v4_import_test_count" =~ ^[0-9]+$ ]]
test "$storage_v4_import_test_count" -eq 1
test "$(grep -Ec '^test result:' "$evidence/storage-v4-import.log")" -eq 1
grep -Fxq "storage_v4_import_live_executed profile=${profile}" "$evidence/storage-v4-import.log"
test "$(grep -Fxc "storage_v4_import_live_executed profile=${profile}" "$evidence/storage-v4-import.log")" -eq 1
if grep -Fq 'storage_v4_import_live_skipped' "$evidence/storage-v4-import.log"; then
  echo 'storage v4 import native fixture skipped instead of executing' >&2
  exit 1
fi
# Preserve every homogeneous mutation occurrence through the real repository.
# This source-DDL fixture is separate from the existing eight-row import packet.
begin_stage storage-duplicate-batches "$evidence/storage-duplicate-batches.log"
# Late Exact counters cover only the two eligibility-excluded Exact cases.
# The separate CR literal-NUL case waits on later Any and retains ACCESS_SQL;
# its local fixture policy must not be counted as a third late Exact case.
case "$profile" in
  postgresql) storage_late_exact_wait_cases=0; storage_late_exact_no_wait_cases=2 ;;
  cockroachdb) storage_late_exact_wait_cases=2; storage_late_exact_no_wait_cases=0 ;;
  *) echo 'unsupported storage native Exact profile' >&2; exit 1 ;;
esac
case "$profile" in
  postgresql) storage_insert_only_committed_wait_cases=0; storage_insert_only_committed_no_wait_cases=1 ;;
  cockroachdb) storage_insert_only_committed_wait_cases=1; storage_insert_only_committed_no_wait_cases=0 ;;
  *) echo 'unsupported storage native insert-only profile' >&2; exit 1 ;;
esac
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" \
TRNM_STORAGE_PINNED_UPSTREAM_DIRECTORY="$evidence_absolute/storage-source-upstream" \
TRNM_STORAGE_TEST_PRODUCER_COMMIT="$candidate_sha" \
TRNM_STORAGE_TEST_PRODUCER_TREE="$candidate_tree" \
  cargo test -p trnm-persistence-pg --locked --test storage_duplicate_batches \
    nakama_duplicate_batches_preserve_step_receipts_and_native_atomicity \
    -- --exact --nocapture --test-threads=1 2>&1 | tee "$evidence/storage-duplicate-batches.log"
storage_duplicate_batches_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/storage-duplicate-batches.log"
)
[[ "$storage_duplicate_batches_test_count" =~ ^[0-9]+$ ]]
test "$storage_duplicate_batches_test_count" -eq 1
test "$(grep -Ec '^test result:' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_duplicate_batches_executed profile=${profile}" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_duplicate_batches_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_duplicate_late_json_rejection profile=${profile} independent_probe_sqlstate=22P02 actual_batch_domain_code=InvalidArgument" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_duplicate_late_json_rejection ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_duplicate_success_full_tuple_executed profile=${profile} fields=15" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_duplicate_success_full_tuple_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_duplicate_go13_executed profile=${profile} occurrences=13 final_ordinals=a12_b0 ack_positions=original fields=15" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_duplicate_go13_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_duplicate_imported_history_executed profile=${profile} source_rows=3 pages=3 witness_null=true full_tuple_fields=15 source_execution_class=native-source-ddl-fixture" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_duplicate_imported_history_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_duplicate_occurrence_locks_executed profile=${profile} missing_delete_rejected_before_late_lock=true fields=15" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_duplicate_occurrence_locks_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_write_tail_drain_executed profile=${profile} held_wait_cases=2 early_reject_cases=3 fields=15" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_write_tail_drain_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_native_insert_only_matrix_executed profile=${profile} cases=3 committed_existing_wait=${storage_insert_only_committed_wait_cases} committed_existing_no_wait=${storage_insert_only_committed_no_wait_cases} uncommitted_delete_wait=1 fields=15" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_native_insert_only_matrix_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_native_jsonb_exact_matrix_executed profile=${profile} cases=11 late_exact_excluded=2 matched_wait_commit=2 missing_exact_drain_wait=1 literal_native_text=1 escaped_nul=1 both_bad_input=1 both_bad_input_vectors=2 surrogate_bind=1 duplicate_exact=1 typed_policy=2 fields=15 late_exact_wait=${storage_late_exact_wait_cases} late_exact_no_wait=${storage_late_exact_no_wait_cases}" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_native_jsonb_exact_matrix_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_native_jsonb_exact_subvector_executed profile=${profile} case=both_bad_input_legal_surrogate main_case=both_bad_payload_token_native_priority input=legal_object_escaped_unpaired_surrogate fields=15 actual_domain=InvalidArgument actual_reason=database_constraint_violation retry=Never no_receipts=true same_lease_readable=true hidden_batch_sqlstate=null" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_native_jsonb_exact_subvector_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_native_any_matrix_executed profile=${profile} cases=4 permission_wait=1 creator_commit=1 unknown_noop=1 surrogate_priority=1 known_duplicate=3 unknown_duplicate=3 aba=2 own_delete_reinsert=1 fields=15 pg_worker_isolation=unobserved" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_native_any_matrix_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
grep -Fxq "nakama_native_any_steps_executed profile=${profile} known_duplicate=3 unknown_duplicate=3 aba=2 own_delete_reinsert=1 fields=15" "$evidence/storage-duplicate-batches.log"
test "$(grep -Ec '^nakama_native_any_steps_executed ' "$evidence/storage-duplicate-batches.log")" -eq 1
if grep -Fq 'nakama_native_any_skipped' "$evidence/storage-duplicate-batches.log"; then
  echo 'storage Any database lane skipped instead of executing' >&2
  exit 1
fi
if grep -Fq 'nakama_native_insert_only_skipped' "$evidence/storage-duplicate-batches.log"; then
  echo 'storage insert-only database lane skipped instead of executing' >&2
  exit 1
fi
if grep -Fq 'nakama_duplicate_batches_skipped' "$evidence/storage-duplicate-batches.log"; then
  echo 'storage duplicate-batch database lane skipped instead of executing' >&2
  exit 1
fi

begin_stage schema-upgrade "$evidence/schema-upgrade.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_SCHEMA_UPGRADE_ADMIN_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --locked --test schema_upgrade \
    -- --nocapture --test-threads=1 2>&1 | tee "$evidence/schema-upgrade.log"
schema_upgrade_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/schema-upgrade.log"
)
[[ "$schema_upgrade_test_count" =~ ^[0-9]+$ ]]
test "$schema_upgrade_test_count" -eq 6
grep -Fxq "schema_v3_shapes_executed profile=${profile} extra_cases=8" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_shapes_executed ' "$evidence/schema-upgrade.log")" -eq 1
grep -Fxq "schema_v3_illegal_legacy_executed profile=${profile} extra_cases=9" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_illegal_legacy_executed ' "$evidence/schema-upgrade.log")" -eq 1
grep -Fxq "schema_v3_catalog_drift_executed profile=${profile} extra_cases=6" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_catalog_drift_executed ' "$evidence/schema-upgrade.log")" -eq 1
grep -Fxq "schema_v3_partial_resume_executed profile=${profile} extra_cases=3" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_partial_resume_executed ' "$evidence/schema-upgrade.log")" -eq 1
grep -Fxq "schema_v3_metadata_validation_executed profile=${profile} extra_cases=9" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_metadata_validation_executed ' "$evidence/schema-upgrade.log")" -eq 1
grep -Fxq "schema_v3_opaque_history_executed profile=${profile} extra_cases=6" "$evidence/schema-upgrade.log"
test "$(grep -Ec '^schema_v3_opaque_history_executed ' "$evidence/schema-upgrade.log")" -eq 1
if grep -Eq 'schema_v3_[a-z_]+_skipped' "$evidence/schema-upgrade.log"; then
  echo 'schema v3 scenario family skipped instead of executing' >&2
  exit 1
fi
if grep -Fq 'developer-only live test skip' "$evidence/schema-upgrade.log"; then
  echo 'schema lifecycle lane skipped instead of executing' >&2
  exit 1
fi
begin_stage schema-verification "$evidence/schema-identity.json" "$evidence/schema-identity-check.json"
TRNM_DATABASE_URL="$database_url" TRNM_DATABASE_PROFILE="$profile" \
  bash scripts/apply-authoritative-schema.sh verify > "$evidence/schema-identity.json"
python3 scripts/check-authoritative-schema-identity.py "$evidence/schema-identity.json" "$profile" --mode verify > "$evidence/schema-identity-check.json"
cp migrations/MIGRATION_CHAIN.lock.json "$evidence/migration-lock.json"
python3 scripts/check-migration-lock.py > "$evidence/migration-chain-validation.json"
python3 scripts/capture-schema-source-selection.py --root "$evidence" --profile "$profile" \
  --commit "$candidate_sha" --tree "$candidate_tree" > "$evidence/schema-source-capture.json"
schema_version=$(db_scalar 'SELECT schema_version FROM trnm_schema_metadata WHERE singleton = 1')
test "$schema_version" = 4
storage_writer_epoch=$(db_scalar 'SELECT storage_writer_epoch FROM trnm_schema_metadata WHERE singleton = 1')
test "$storage_writer_epoch" = 4
v2_apply_source_commit=$(db_scalar 'SELECT v2_apply_source_commit FROM trnm_schema_metadata WHERE singleton = 1')
test "$v2_apply_source_commit" = "$candidate_sha"
v3_apply_source_commit=$(db_scalar 'SELECT v3_apply_source_commit FROM trnm_schema_metadata WHERE singleton = 1')
test "$v3_apply_source_commit" = "$candidate_sha"
python3 - "$evidence" "$profile" "$candidate_sha" <<'PY_STORAGE_SCHEMA_V3'
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,"scripts")
import schema_evidence_binding as binding

evidence,profile,candidate=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
identity=json.loads((evidence/'schema-identity.json').read_text())
assert identity['profile']==profile
assert identity['schema_version']==4 and identity['storage_writer_epoch']==4
assert identity['source_commit']==identity['upgrade_source_commit']==identity['v2_apply_source_commit']==identity['v3_apply_source_commit']==candidate
lock=json.loads((evidence/'migration-lock.json').read_text())
assert type(lock['schema_version']) is int and lock['schema_version']==5
token=binding.verify_binding(Path('.'),profile=profile)
files=binding.operational_binding(token)['ordered_files']
assert len(lock['profiles'][profile]['ordered_files'])==5
names=('0001_foundation_up.sql','0002_storage_timestamps_up.sql','0003_storage_jsonb_up.sql','0004_storage_source_import_up.sql')
assert [entry['path'] for entry in files]==[f'migrations/{profile}/{name}' for name in names]
archive=evidence/'authoritative-migrations'
archive.mkdir()
sealed=[]
for entry,name in zip(files,names,strict=True):
    data=Path(entry['path']).read_bytes()
    assert data and hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()==entry['git_blob_sha1']
    target=archive/name
    target.write_bytes(data)
    assert target.read_bytes()==data
    sealed.append({'path':entry['path'],'archive_path':str(target.relative_to(evidence)),
                   'git_blob_sha1':entry['git_blob_sha1'],'sha256':hashlib.sha256(data).hexdigest(),
                   'size_bytes':len(data)})
manifest={'schema':'trillionnium.server-live-schema-source.v1','profile':profile,
          'schema_version':identity['schema_version'],'storage_writer_epoch':identity['storage_writer_epoch'],
          'ordered_files':sealed,'compatibility_credit':False}
(evidence/'authoritative-migrations.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n')
PY_STORAGE_SCHEMA_V3
authoritative_migrations_count=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["ordered_files"]))' "$evidence/authoritative-migrations.json")
test "$authoritative_migrations_count" -eq 4

begin_stage session-response-loss "$evidence/session-response-loss.log"
CARGO_TERM_COLOR=never \
TRNM_REQUIRE_LIVE_DATABASE=1 \
TRNM_DATABASE_URL="$database_url" \
TRNM_DATABASE_PROFILE="$profile" \
  cargo test -p trnm-persistence-pg --features session-test-hooks --locked \
    --test session_response_loss -- --nocapture --test-threads=1 2>&1 | tee "$evidence/session-response-loss.log"
session_test_count=$(
  sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' \
    "$evidence/session-response-loss.log"
)
[[ "$session_test_count" =~ ^[0-9]+$ ]]
test "$session_test_count" -ge 8

response_loss_family_hex=$(printf '71%.0s' {1..16})
test "$(db_scalar "SELECT count(*) FROM trnm_session_families WHERE family_id = decode('${response_loss_family_hex}', 'hex')")" = 1
test "$(db_scalar "SELECT count(*) FROM trnm_refresh_tokens WHERE family_id = decode('${response_loss_family_hex}', 'hex')")" = 2
test "$(db_scalar "SELECT count(*) FROM trnm_session_families WHERE family_id = decode('${response_loss_family_hex}', 'hex') AND active_token_id IS NULL AND revoked_reason = 2")" = 1

concurrency_logout_families=0
for family_byte in 90 91 92 93 94 95 96 97 98 99 9a 9b 9c 9d 9e 9f; do
  family_hex=$(printf "${family_byte}%.0s" {1..16})
  test "$(db_scalar "SELECT count(*) FROM trnm_session_families WHERE family_id = decode('${family_hex}', 'hex') AND active_token_id IS NULL AND revoked_reason = 0")" = 1
  concurrency_logout_families=$((concurrency_logout_families + 1))
done
test "$concurrency_logout_families" = 16

assert_interleaving_family() {
  local family_byte=$1
  local generation=$2
  local active_byte=$3
  local revoked_reason=$4
  local token_count=$5
  local active_count=$6
  local family_hex active_clause revocation_clause active_hex
  family_hex=$(printf "${family_byte}%.0s" {1..16})
  if [[ "$active_byte" == none ]]; then
    active_clause='active_token_id IS NULL'
  else
    active_hex=$(printf "${active_byte}%.0s" {1..16})
    active_clause="active_token_id = decode('${active_hex}', 'hex')"
  fi
  if [[ "$revoked_reason" == none ]]; then
    revocation_clause='revoked_reason IS NULL'
  else
    revocation_clause="revoked_reason = ${revoked_reason}"
  fi
  test "$(db_scalar "SELECT count(*) FROM trnm_session_families WHERE family_id = decode('${family_hex}', 'hex') AND generation = ${generation} AND ${active_clause} AND ${revocation_clause}")" = 1
  test "$(db_scalar "SELECT count(*) FROM trnm_refresh_tokens WHERE family_id = decode('${family_hex}', 'hex')")" = "$token_count"
  test "$(db_scalar "SELECT count(*) FROM trnm_refresh_tokens WHERE family_id = decode('${family_hex}', 'hex') AND state = 0")" = "$active_count"
}

assert_interleaving_family 40 1 44 none 2 1
assert_interleaving_family 46 2 4c none 3 1
assert_interleaving_family 4e 2 none 2 3 0
assert_interleaving_family 56 1 none 2 2 0
assert_interleaving_family 5e 1 none 2 2 0
interleaving_family_count=5

start_server() {
  phase=$1
  begin_stage "server-${phase}" "$evidence/server-${phase}.log" "$evidence/client-${phase}.log"
  "$binary" serve > "$evidence/server-${phase}.log" 2>&1 &
  server_pid=$!
  printf '%s\n' "$server_pid" > "$evidence/server-${phase}.pid"
}

wait_for_server_exit() {
  phase=$1
  for _ in $(seq 1 120); do
    if ! kill -0 "$server_pid" 2>/dev/null; then
      wait "$server_pid"
      server_pid=''
      return 0
    fi
    sleep 0.1
  done
  echo "server did not exit after ${phase} drain" >&2
  return 1
}

start_server primary
python3 scripts/trnm-server-live-client.py \
  --port "$server_port" \
  --token "$admin_token" \
  --phase primary \
  --output "$evidence/client-primary.json" \
  2>&1 | tee "$evidence/client-primary.log"
wait_for_server_exit primary

start_server restart
python3 scripts/trnm-server-live-client.py \
  --port "$server_port" \
  --token "$admin_token" \
  --phase restart \
  --output "$evidence/client-restart.json" \
  2>&1 | tee "$evidence/client-restart.log"
wait_for_server_exit restart

begin_stage server-credential-check "$evidence/server-primary.log" "$evidence/server-restart.log"
if grep -Fq 'trnm_live_password' "$evidence"/server-*.log; then
  echo 'database credential leaked by server log' >&2
  exit 1
fi
if grep -Fq "$admin_token" "$evidence"/server-*.log; then
  echo 'admin token leaked by server log' >&2
  exit 1
fi

begin_stage database-assertions "$evidence/database-assertions.txt"
entity_hex=$(printf '01%.0s' {1..16})
state=$(db_scalar "SELECT revision || '|' || last_event_sequence FROM trnm_entity_heads WHERE entity_id = decode('${entity_hex}', 'hex')")
test "$state" = '3|3'
printf 'entity_head=%s\n' "$state" > "$evidence/database-assertions.txt"

for table in trnm_entity_heads trnm_command_receipts trnm_events trnm_outbox trnm_command_outbox; do
  count=$(db_scalar "SELECT count(*) FROM ${table}")
  case "$table" in
    trnm_entity_heads) expected=1 ;;
    *) expected=3 ;;
  esac
  test "$count" = "$expected"
  printf '%s=%s\n' "$table" "$count" >> "$evidence/database-assertions.txt"
done
pending=$(db_scalar 'SELECT count(*) FROM trnm_outbox WHERE state = 0')
test "$pending" = '3'
printf 'pending_outbox=%s\n' "$pending" >> "$evidence/database-assertions.txt"
source_commit=$(db_scalar 'SELECT source_commit FROM trnm_schema_metadata WHERE singleton = 1')
test "$source_commit" = "$candidate_sha"
printf 'schema_source_commit=%s\n' "$source_commit" >> "$evidence/database-assertions.txt"
printf 'schema_version=%s\nstorage_writer_epoch=%s\nv2_apply_source_commit=%s\nauthoritative_migrations_count=%s\n' \
  "$schema_version" "$storage_writer_epoch" "$v2_apply_source_commit" "$authoritative_migrations_count" \
  >> "$evidence/database-assertions.txt"
printf 'session_test_count=%s\n' "$session_test_count" \
  >> "$evidence/database-assertions.txt"
printf 'response_loss_family_rows=%s\n' \
  "$(db_scalar "SELECT count(*) FROM trnm_session_families WHERE family_id = decode('${response_loss_family_hex}', 'hex')")" \
  >> "$evidence/database-assertions.txt"
printf 'response_loss_refresh_tokens=%s\n' \
  "$(db_scalar "SELECT count(*) FROM trnm_refresh_tokens WHERE family_id = decode('${response_loss_family_hex}', 'hex')")" \
  >> "$evidence/database-assertions.txt"
printf 'response_loss_replay_revoked=%s\n' \
  "$(db_scalar "SELECT count(*) FROM trnm_session_families WHERE family_id = decode('${response_loss_family_hex}', 'hex') AND active_token_id IS NULL AND revoked_reason = 2")" \
  >> "$evidence/database-assertions.txt"
printf 'concurrency_logout_families=%s\n' "$concurrency_logout_families" \
  >> "$evidence/database-assertions.txt"
printf 'deterministic_interleaving_families=%s\n' "$interleaving_family_count" \
  >> "$evidence/database-assertions.txt"
printf 'diagnostic_total_session_families=%s\n' \
  "$(db_scalar 'SELECT count(*) FROM trnm_session_families')" \
  >> "$evidence/database-assertions.txt"
printf 'diagnostic_total_refresh_tokens=%s\n' \
  "$(db_scalar 'SELECT count(*) FROM trnm_refresh_tokens')" \
  >> "$evidence/database-assertions.txt"

# Separate disposable raw SQL5 fixture packet; never changes the live trnm DB.
# Opted in only by the hosted profile workflow; no AccountsV5 runtime gate change.
if [[ "${TRNM_ACCOUNTS_CAPTURE_DIAGNOSTIC:-0}" == 1 ]]; then
  begin_stage accounts-capture-diagnostic
  python3 scripts/accounts-capture-diagnostic.py capture --profile "$profile" \
    --container "$container" --parent "$evidence_absolute" \
    --output "$root/run/accounts-capture/$profile"
fi

begin_stage seal "$evidence/summary.json" "$evidence/database-assertions.txt"
cat > "$evidence/summary.json" <<EOF
{"schema":"trillionnium.server-live-evidence.v1","repository":"TrillionniumFoundation/TrillionniumGame","commit":"${candidate_sha}","tree":"${candidate_tree}","profile":"${profile}","check_config":true,"fresh_migration":true,"nakama_client_list_projection":true,"storage_timestamps":true,"storage_occ_precedence":true,"raw_version_conditions":true,"storage_jsonb_v3_projection":true,"storage_native_jsonb":true,"storage_v4_acl":true,"storage_v4_import":true,"storage_homogeneous_batches":true,"storage_homogeneous_app":true,"storage_write_tail_drain":true,"storage_native_jsonb_exact":true,"storage_native_insert_only":true,"storage_native_any":true,"any_native_main_cases":4,"any_known_duplicate_occurrences":3,"any_unknown_duplicate_occurrences":3,"any_aba_occurrences":2,"any_delete_reinsert_cases":1,"insert_only_committed_wait_cases":${storage_insert_only_committed_wait_cases},"insert_only_committed_no_wait_cases":${storage_insert_only_committed_no_wait_cases},"insert_only_uncommitted_delete_wait_cases":1,"late_exact_wait_cases":${storage_late_exact_wait_cases},"late_exact_no_wait_cases":${storage_late_exact_no_wait_cases},"storage_jsonb_native_write_failure":true,"schema_v3_extra_cases":41,"schema_v3_case_families":{"shapes":8,"illegal_legacy":9,"catalog_drift":6,"partial_resume":3,"metadata_validation":9,"opaque_history":6},"storage_jsonb_v3_cases":{"history":6,"opaque_success":4,"no_op":2,"resource":1,"native_input":3},"schema_version":${schema_version},"storage_writer_epoch":${storage_writer_epoch},"authoritative_migrations_count":${authoritative_migrations_count},"schema_upgrade":true,"health_ready":true,"unauthenticated_mutation_rejected":true,"http_bootstrap_commit_duplicate_conflict":true,"websocket_json_commit":true,"response_loss_exact_receipt_replay":true,"refresh_response_loss_exact_successor_replay":true,"refresh_changed_successor_revoked_family":true,"refresh_logout_concurrency_deadlock_free":true,"authenticated_drain":true,"process_restart_exact_receipt_replay":true,"entity_revision":3,"event_sequence":3,"command_receipts":3,"events":3,"outbox_intents":3,"production_pitr":false,"multi_node":false,"wire_compatible":false,"compatibility_credit":false,"accepted":false,"production_ready":false}
EOF
python3 -m json.tool "$evidence/summary.json" >/dev/null
find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum > "$evidence/SHA256SUMS"
cat "$evidence/summary.json"
cat "$evidence/database-assertions.txt"
echo "trnm-server live contract passed: profile=${profile} evidence=${evidence}"
