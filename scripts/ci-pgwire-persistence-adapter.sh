#!/usr/bin/env bash
set -uo pipefail

mode=${1:-}
evidence_root=${TRNM_EVIDENCE_ROOT:-pgwire-evidence}
source_commit=${TRNM_SCHEMA_SOURCE_COMMIT:-e9b63462fa91383b06706894afed31b378f6b48c}

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
    evidence="$evidence_root/postgresql"
    mkdir -p "$evidence"
    git rev-parse HEAD > "$evidence/commit.txt"
    git rev-parse HEAD^{tree} > "$evidence/tree.txt"
    cargo generate-lockfile > "$evidence/cargo-generate-lockfile.log" 2>&1
    lock_status=$?
    docker pull postgres:16.4-bookworm > "$evidence/image-pull.log" 2>&1
    pull_status=$?
    start_status=1
    ready_status=1
    apply_status=1
    test_status=1
    if (( pull_status == 0 )); then
      docker run --detach --name trnm-pgwire-postgresql -p 5432:5432 \
        -e POSTGRES_USER=trnm -e POSTGRES_PASSWORD=trnm -e POSTGRES_DB=trnm \
        postgres:16.4-bookworm > "$evidence/container-start.log" 2>&1
      start_status=$?
    fi
    if (( start_status == 0 )); then
      wait_postgresql trnm-pgwire-postgresql
      ready_status=$?
      docker inspect trnm-pgwire-postgresql > "$evidence/container-inspect.json" 2>&1
      docker logs trnm-pgwire-postgresql > "$evidence/container.log" 2>&1
    fi
    if (( ready_status == 0 )); then
      docker exec -i trnm-pgwire-postgresql psql -X -v ON_ERROR_STOP=1 -U trnm -d trnm \
        < migrations/postgresql/0001_foundation_up.sql > "$evidence/apply.log" 2>&1
      apply_status=$?
    fi
    if (( apply_status == 0 && lock_status == 0 )); then
      TRNM_DATABASE_URL='postgresql://trnm:trnm@127.0.0.1:5432/trnm' \
      TRNM_DATABASE_PROFILE='postgresql' \
      TRNM_SCHEMA_SOURCE_COMMIT="$source_commit" \
        cargo test -p trnm-persistence-pg --test runtime -- --nocapture \
        > "$evidence/runtime-test.log" 2>&1
      test_status=$?
    fi
    record_summary "$evidence" postgresql \
      "generate_lockfile=$lock_status" "image_pull=$pull_status" \
      "container_start=$start_status" "database_ready=$ready_status" \
      "migration_apply=$apply_status" "live_runtime_test=$test_status"
    seal_evidence "$evidence"
    (( lock_status || pull_status || start_status || ready_status || apply_status || test_status )) && exit 1
    ;;

  cockroachdb)
    evidence="$evidence_root/cockroachdb"
    mkdir -p "$evidence"
    git rev-parse HEAD > "$evidence/commit.txt"
    git rev-parse HEAD^{tree} > "$evidence/tree.txt"
    cargo generate-lockfile > "$evidence/cargo-generate-lockfile.log" 2>&1
    lock_status=$?
    docker pull cockroachdb/cockroach:v24.1.2 > "$evidence/image-pull.log" 2>&1
    pull_status=$?
    start_status=1
    ready_status=1
    create_status=1
    apply_status=1
    test_status=1
    if (( pull_status == 0 )); then
      docker run --detach --name trnm-pgwire-cockroachdb --network host \
        cockroachdb/cockroach:v24.1.2 \
        start-single-node --insecure --listen-addr=127.0.0.1:26257 \
        --http-addr=127.0.0.1:8080 > "$evidence/container-start.log" 2>&1
      start_status=$?
    fi
    if (( start_status == 0 )); then
      wait_cockroachdb trnm-pgwire-cockroachdb
      ready_status=$?
      docker inspect trnm-pgwire-cockroachdb > "$evidence/container-inspect.json" 2>&1
      docker logs trnm-pgwire-cockroachdb > "$evidence/container.log" 2>&1
    fi
    if (( ready_status == 0 )); then
      docker exec trnm-pgwire-cockroachdb /cockroach/cockroach sql --insecure \
        --host=127.0.0.1:26257 --execute='CREATE DATABASE trnm' \
        > "$evidence/create-database.log" 2>&1
      create_status=$?
    fi
    if (( create_status == 0 )); then
      docker exec -i trnm-pgwire-cockroachdb /cockroach/cockroach sql --insecure \
        --host=127.0.0.1:26257 --database=trnm \
        < migrations/cockroachdb/0001_foundation_up.sql > "$evidence/apply.log" 2>&1
      apply_status=$?
    fi
    if (( apply_status == 0 && lock_status == 0 )); then
      TRNM_DATABASE_URL='postgresql://root@127.0.0.1:26257/trnm?sslmode=disable' \
      TRNM_DATABASE_PROFILE='cockroachdb' \
      TRNM_SCHEMA_SOURCE_COMMIT="$source_commit" \
        cargo test -p trnm-persistence-pg --test runtime -- --nocapture \
        > "$evidence/runtime-test.log" 2>&1
      test_status=$?
    fi
    record_summary "$evidence" cockroachdb \
      "generate_lockfile=$lock_status" "image_pull=$pull_status" \
      "container_start=$start_status" "database_ready=$ready_status" \
      "create_database=$create_status" "migration_apply=$apply_status" \
      "live_runtime_test=$test_status"
    seal_evidence "$evidence"
    (( lock_status || pull_status || start_status || ready_status || create_status || apply_status || test_status )) && exit 1
    ;;

  *)
    printf 'usage: %s {materialize|postgresql|cockroachdb}\n' "$0" >&2
    exit 64
    ;;
esac
