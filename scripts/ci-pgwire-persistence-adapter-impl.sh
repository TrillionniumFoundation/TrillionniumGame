#!/usr/bin/env bash
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

pinned_image_for() {
  python3 - "$1" <<'PYIMAGE'
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

mode=${1:-}
evidence_root=${TRNM_EVIDENCE_ROOT:-pgwire-evidence}
source_commit=${TRNM_SCHEMA_SOURCE_COMMIT:-$(git rev-parse HEAD)}

capture_image_identity() {
  local evidence=$1 image=$2
  docker image inspect "$image" >"$evidence/image-inspect.json" 2>&1 || return
  image_id=$(docker image inspect --format '{{.Id}}' "$image") || return
  image_repo_digests=$(docker image inspect --format '{{json .RepoDigests}}' "$image") || return
  printf 'image=%s\nimage_id=%s\nrepo_digests=%s\n' "$image" "$image_id" "$image_repo_digests" >"$evidence/image.txt"
}

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

apply_authoritative_schema() {
  local evidence=$1 profile=$2 database_url=$3
  python3 scripts/check-migration-lock.py >"$evidence/migration-chain-validation.json" || return
  cp migrations/MIGRATION_CHAIN.lock.json "$evidence/migration-chain.lock.json" || return
  TRNM_DATABASE_URL="$database_url" \
  TRNM_DATABASE_PROFILE="$profile" \
  TRNM_SCHEMA_SOURCE_COMMIT="$source_commit" \
  TRNM_SCHEMA_APPLIED_AT_MS=1 \
    bash scripts/apply-authoritative-schema.sh migrate \
    >"$evidence/schema-identity.json" 2>"$evidence/schema-migration.log" || return
  python3 scripts/check-authoritative-schema-identity.py "$evidence/schema-identity.json" "$profile" \
    --mode fresh --source-commit="$source_commit" >"$evidence/schema-identity-check.json"
}

record_summary() {
  local evidence=$1
  local profile=$2
  shift 2
  python3 - "$evidence" "$profile" "$@" <<'PY'
import json
import sys
from pathlib import Path

evidence = Path(sys.argv[1])
profile = sys.argv[2]
statuses = {}
for item in sys.argv[3:]:
    key, value = item.split("=", 1)
    statuses[key] = int(value)
payload = {
    "schema": "trillionnium.game.pgwire-adapter-ci.v2",
    "profile": profile,
    "target_commit": (evidence / "commit.txt").read_text(encoding="utf-8").strip(),
    "statuses": statuses,
    "image_identity": (evidence / "image.txt").read_text().splitlines() if (evidence / "image.txt").is_file() else None,
    "schema_identity": json.loads((evidence / "schema-identity.json").read_text()) if statuses.get("migration_apply") == 0 else None,
    "migration_chain_validation": json.loads((evidence / "migration-chain-validation.json").read_text()) if statuses.get("migration_apply") == 0 else None,
    "all_passed": all(value == 0 for value in statuses.values()),
    "claims": {
        "reconnect_replay_verified": profile in {"postgresql", "cockroachdb"}
        and statuses.get("live_runtime_test") == 0,
        "ha_verified": False,
        "production_tls_verified": False,
        "production_ready": False,
    },
}
(evidence / "summary.json").write_text(
    json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
}

seal_evidence() {
  local evidence=$1
  find "$evidence" -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum > "$evidence/SHA256SUMS"
}

wait_postgresql() {
  local container=$1
  for _ in $(seq 1 90); do
    if docker exec "$container" psql -X -v ON_ERROR_STOP=1 -U trnm -d trnm \
      -c 'SELECT 1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_cockroachdb() {
  local container=$1
  for _ in $(seq 1 120); do
    if docker exec "$container" /cockroach/cockroach sql --insecure \
      --host=127.0.0.1:26257 --execute='SELECT 1' >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

case "$mode" in
  materialize)
    evidence="$evidence_root/materialize"
    mkdir -p "$evidence/materialized"
    git rev-parse HEAD > "$evidence/commit.txt"
    git rev-parse HEAD^{tree} > "$evidence/tree.txt"
    rustc --version --verbose > "$evidence/rustc-version.txt"
    cargo --version --verbose > "$evidence/cargo-version.txt"

    cargo generate-lockfile > "$evidence/cargo-generate-lockfile.log" 2>&1
    lock_status=$?
    cargo fmt --all > "$evidence/cargo-fmt-apply.log" 2>&1
    fmt_apply_status=$?
    git diff --binary > "$evidence/materialization.patch"
    git diff --name-only > "$evidence/materialized-files.txt"
    while IFS= read -r path; do
      [[ -z "$path" ]] && continue
      mkdir -p "$evidence/materialized/$(dirname "$path")"
      cp "$path" "$evidence/materialized/$path"
    done < "$evidence/materialized-files.txt"
    cargo fmt --all -- --check > "$evidence/cargo-fmt-check.log" 2>&1
    fmt_check_status=$?
    cargo test --workspace --all-targets > "$evidence/cargo-test.log" 2>&1
    test_status=$?
    cargo clippy --workspace --all-targets -- -D warnings > "$evidence/cargo-clippy.log" 2>&1
    clippy_status=$?
    python3 -m compileall -q scripts > "$evidence/python-compileall.log" 2>&1
    python_status=$?
    python3 scripts/check-pgwire-persistence-adapter.py > "$evidence/static-contract.log" 2>&1
    contract_status=$?

    record_summary "$evidence" materialize \
      "generate_lockfile=$lock_status" \
      "cargo_fmt_apply=$fmt_apply_status" \
      "cargo_fmt_check=$fmt_check_status" \
      "cargo_test=$test_status" \
      "cargo_clippy=$clippy_status" \
      "python_compileall=$python_status" \
      "static_contract=$contract_status"
    seal_evidence "$evidence"
    (( lock_status || fmt_apply_status || fmt_check_status || test_status || clippy_status || python_status || contract_status )) && exit 1
    ;;

  postgresql)
    image=$(pinned_image_for postgresql) || exit 1
    requested_image=${TRNM_POSTGRES_IMAGE:-$image}
    if [[ "$requested_image" != "$image" ]]; then
      echo 'database image override does not match config/database-test-images.json' >&2
      exit 64
    fi
    evidence="$evidence_root/postgresql"
    mkdir -p "$evidence"
    git rev-parse HEAD > "$evidence/commit.txt"
    git rev-parse HEAD^{tree} > "$evidence/tree.txt"
    cargo generate-lockfile > "$evidence/cargo-generate-lockfile.log" 2>&1
    lock_status=$?
    docker pull "$image" > "$evidence/image-pull.log" 2>&1
    pull_status=$?
    image_identity_status=1
    if (( pull_status == 0 )); then
      capture_image_identity "$evidence" "$image"
      image_identity_status=$?
    fi
    start_status=1
    ready_status=1
    running_image_status=1
    apply_status=1
    test_status=1
    if (( pull_status == 0 && image_identity_status == 0 )); then
      docker run --detach --name trnm-pgwire-postgresql -p 5432:5432 \
        -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm \
        "$image" > "$evidence/container-start.log" 2>&1
      start_status=$?
    fi
    if (( start_status == 0 )); then
      wait_postgresql trnm-pgwire-postgresql
      ready_status=$?
      docker inspect trnm-pgwire-postgresql > "$evidence/container-inspect.json" 2>&1
      docker logs trnm-pgwire-postgresql > "$evidence/container.log" 2>&1
      verify_running_image postgresql trnm-pgwire-postgresql "$image_id" "$evidence"
      running_image_status=$?
    fi
    if (( ready_status == 0 && running_image_status == 0 )); then
      apply_authoritative_schema "$evidence" postgresql 'postgresql://trnm:trnm@127.0.0.1:5432/trnm'
      apply_status=$?
    fi
    if (( apply_status == 0 && lock_status == 0 )); then
      CARGO_TERM_COLOR=never \
      TRNM_REQUIRE_LIVE_DATABASE=1 \
      TRNM_DATABASE_URL='postgresql://trnm:trnm@127.0.0.1:5432/trnm' \
      TRNM_DATABASE_PROFILE='postgresql' \
      TRNM_SCHEMA_SOURCE_COMMIT="$source_commit" \
        cargo test -p trnm-persistence-pg --test runtime -- --nocapture \
        > "$evidence/runtime-test.log" 2>&1
      test_status=$?
      if (( test_status == 0 )); then
        runtime_test_count=$(sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' "$evidence/runtime-test.log")
        printf '%s\n' "$runtime_test_count" >"$evidence/runtime-test-count.txt"
        if [[ ! "$runtime_test_count" =~ ^[1-9][0-9]*$ ]] \
          || grep -q 'developer-only live test skip' "$evidence/runtime-test.log"; then
          test_status=1
        fi
      fi
    fi
    record_summary "$evidence" postgresql \
      "generate_lockfile=$lock_status" "image_pull=$pull_status" "image_identity=$image_identity_status" \
      "container_start=$start_status" "database_ready=$ready_status" "running_image=$running_image_status" \
      "migration_apply=$apply_status" "live_runtime_test=$test_status"
    seal_evidence "$evidence"
    (( lock_status || pull_status || image_identity_status || start_status || ready_status || running_image_status || apply_status || test_status )) && exit 1
    ;;

  cockroachdb)
    image=$(pinned_image_for cockroachdb) || exit 1
    requested_image=${TRNM_COCKROACH_IMAGE:-$image}
    if [[ "$requested_image" != "$image" ]]; then
      echo 'database image override does not match config/database-test-images.json' >&2
      exit 64
    fi
    evidence="$evidence_root/cockroachdb"
    mkdir -p "$evidence"
    git rev-parse HEAD > "$evidence/commit.txt"
    git rev-parse HEAD^{tree} > "$evidence/tree.txt"
    cargo generate-lockfile > "$evidence/cargo-generate-lockfile.log" 2>&1
    lock_status=$?
    docker pull "$image" > "$evidence/image-pull.log" 2>&1
    pull_status=$?
    image_identity_status=1
    if (( pull_status == 0 )); then
      capture_image_identity "$evidence" "$image"
      image_identity_status=$?
    fi
    start_status=1
    ready_status=1
    running_image_status=1
    create_status=1
    apply_status=1
    test_status=1
    if (( pull_status == 0 && image_identity_status == 0 )); then
      docker run --detach --name trnm-pgwire-cockroachdb --network host \
        "$image" \
        start-single-node --insecure --listen-addr=127.0.0.1:26257 \
        --http-addr=127.0.0.1:8080 > "$evidence/container-start.log" 2>&1
      start_status=$?
    fi
    if (( start_status == 0 )); then
      wait_cockroachdb trnm-pgwire-cockroachdb
      ready_status=$?
      docker inspect trnm-pgwire-cockroachdb > "$evidence/container-inspect.json" 2>&1
      docker logs trnm-pgwire-cockroachdb > "$evidence/container.log" 2>&1
      verify_running_image cockroachdb trnm-pgwire-cockroachdb "$image_id" "$evidence"
      running_image_status=$?
    fi
    if (( ready_status == 0 && running_image_status == 0 )); then
      docker exec trnm-pgwire-cockroachdb /cockroach/cockroach sql --insecure \
        --host=127.0.0.1:26257 --execute='CREATE DATABASE trnm' \
        > "$evidence/create-database.log" 2>&1
      create_status=$?
    fi
    if (( create_status == 0 )); then
      apply_authoritative_schema "$evidence" cockroachdb 'postgresql://root@127.0.0.1:26257/trnm?sslmode=disable'
      apply_status=$?
    fi
    if (( apply_status == 0 && lock_status == 0 )); then
      CARGO_TERM_COLOR=never \
      TRNM_REQUIRE_LIVE_DATABASE=1 \
      TRNM_DATABASE_URL='postgresql://root@127.0.0.1:26257/trnm?sslmode=disable' \
      TRNM_DATABASE_PROFILE='cockroachdb' \
      TRNM_SCHEMA_SOURCE_COMMIT="$source_commit" \
        cargo test -p trnm-persistence-pg --test runtime -- --nocapture \
        > "$evidence/runtime-test.log" 2>&1
      test_status=$?
      if (( test_status == 0 )); then
        runtime_test_count=$(sed -nE 's/^test result: ok[.] ([0-9]+) passed; 0 failed; 0 ignored;.*/\1/p' "$evidence/runtime-test.log")
        printf '%s\n' "$runtime_test_count" >"$evidence/runtime-test-count.txt"
        if [[ ! "$runtime_test_count" =~ ^[1-9][0-9]*$ ]] \
          || grep -q 'developer-only live test skip' "$evidence/runtime-test.log"; then
          test_status=1
        fi
      fi
    fi
    record_summary "$evidence" cockroachdb \
      "generate_lockfile=$lock_status" "image_pull=$pull_status" "image_identity=$image_identity_status" \
      "container_start=$start_status" "database_ready=$ready_status" "running_image=$running_image_status" \
      "create_database=$create_status" "migration_apply=$apply_status" \
      "live_runtime_test=$test_status"
    seal_evidence "$evidence"
    (( lock_status || pull_status || image_identity_status || start_status || ready_status || running_image_status || create_status || apply_status || test_status )) && exit 1
    ;;

  *)
    printf 'usage: %s {materialize|postgresql|cockroachdb}\n' "$0" >&2
    exit 64
    ;;
esac
