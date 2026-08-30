#!/usr/bin/env bash
set -Eeuo pipefail

profile=${1:-}
case "$profile" in
  postgresql|cockroachdb) ;;
  *)
    echo 'usage: ci-pgwire-vertical-slice.sh postgresql|cockroachdb' >&2
    exit 64
    ;;
esac

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

for command in docker python3 cargo git sha256sum; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required" >&2
    exit 69
  }
done

case "$profile" in
  postgresql)
    image=${TRNM_POSTGRES_IMAGE:?TRNM_POSTGRES_IMAGE with immutable OCI digest is required}
    migration=migrations/postgresql/0001_foundation_up.sql
    ;;
  cockroachdb)
    image=${TRNM_COCKROACH_IMAGE:?TRNM_COCKROACH_IMAGE with immutable OCI digest is required}
    migration=migrations/cockroachdb/0001_foundation_up.sql
    ;;
esac

case "$image" in
  *@sha256:[0-9a-f][0-9a-f]*) ;;
  *)
    echo "$profile image must include @sha256:<digest>" >&2
    exit 64
    ;;
esac

test -f "$migration"
commit=$(git rev-parse HEAD)
tree=$(git rev-parse HEAD^{tree})
migration_sha=$(sha256sum "$migration" | awk '{print $1}')
run_id=${TRNM_RUN_ID:-local-$(date -u +%Y%m%dT%H%M%SZ)-$$}
evidence_root=${TRNM_EVIDENCE_ROOT:-run/pgwire-vertical-slice}
evidence="$evidence_root/$profile/$run_id"
mkdir -p "$evidence/logs"
container="trnm-pgwire-${profile}-${run_id//[^a-zA-Z0-9_.-]/-}"
password='trnm-local-evidence-password-0123456789'

container_exists() {
  docker inspect "$container" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]]
}

capture_container_diagnostics() {
  if container_exists; then
    docker inspect "$container" >"$evidence/logs/container-inspect.json" 2>&1 || true
    docker logs "$container" >"$evidence/logs/container.log" 2>&1 || true
  fi
}

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}

on_error() {
  status=$?
  trap - ERR
  set +e
  capture_container_diagnostics
  if [[ -s "$evidence/logs/container.log" ]]; then
    echo "--- $profile container log tail ---" >&2
    tail -n 200 "$evidence/logs/container.log" >&2
  fi
  exit "$status"
}

trap on_error ERR
trap cleanup EXIT INT TERM

printf '%s\n' \
  "repository=TrillionniumFoundation/TrillionniumGame" \
  "commit=$commit" \
  "tree=$tree" \
  "profile=$profile" \
  "image=$image" \
  "migration=$migration" \
  "migration_sha256=$migration_sha" \
  "run_id=$run_id" \
  >"$evidence/source.txt"

docker pull "$image" 2>&1 | tee "$evidence/logs/image-pull.log"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
image_repo_digests=$(docker image inspect --format '{{json .RepoDigests}}' "$image")
printf 'image_id=%s\nrepo_digests=%s\n' "$image_id" "$image_repo_digests" \
  >"$evidence/image.txt"

if [[ "$profile" == postgresql ]]; then
  docker run -d --name "$container" \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD="$password" \
    -e POSTGRES_DB=trnm \
    -p 127.0.0.1::5432 \
    "$image" >"$evidence/container-id.txt"

  ready=false
  for _ in $(seq 1 120); do
    container_running || break
    # The official image starts a temporary initialization server and then
    # restarts PostgreSQL. Requiring the second readiness log prevents a race
    # where migration connects while that temporary server is shutting down.
    ready_count=$(docker logs "$container" 2>&1 \
      | grep -c 'database system is ready to accept connections' || true)
    if (( ready_count >= 2 )) \
      && docker exec -e PGPASSWORD="$password" "$container" \
        psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm \
        -c 'SELECT 1' 2>/dev/null | grep -qx '1'; then
      ready=true
      break
    fi
    sleep 1
  done
  if [[ "$ready" != true ]]; then
    echo 'PostgreSQL did not reach stable post-initialization readiness' >&2
    false
  fi

  port=$(docker port "$container" 5432/tcp | awk -F: 'NR==1 {print $NF}')
  [[ "$port" =~ ^[0-9]+$ ]]
  database_url="postgresql://postgres:${password}@127.0.0.1:${port}/trnm"
  docker exec -i -e PGPASSWORD="$password" "$container" \
    psql -X -v ON_ERROR_STOP=1 -U postgres -d trnm \
    <"$migration" 2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec -e PGPASSWORD="$password" "$container" \
      psql -X -A -t -v ON_ERROR_STOP=1 -U postgres -d trnm -c "$1"
  }
else
  docker run -d --name "$container" \
    -p 127.0.0.1::26257 \
    "$image" start-single-node \
      --insecure \
      --listen-addr=0.0.0.0:26257 \
      --advertise-addr=127.0.0.1:26257 \
      --http-addr=0.0.0.0:8080 \
      --store=type=mem,size=1GiB \
      --cache=128MiB \
      --max-sql-memory=128MiB \
    >"$evidence/container-id.txt"

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
  if [[ "$ready" != true ]]; then
    echo 'CockroachDB did not reach SQL readiness' >&2
    false
  fi

  docker exec "$container" cockroach sql --insecure \
    --host=127.0.0.1:26257 --execute='CREATE DATABASE IF NOT EXISTS trnm'
  port=$(docker port "$container" 26257/tcp | awk -F: 'NR==1 {print $NF}')
  [[ "$port" =~ ^[0-9]+$ ]]
  database_url="postgresql://root@127.0.0.1:${port}/trnm?sslmode=disable"
  docker exec -i "$container" cockroach sql --insecure \
    --host=127.0.0.1:26257 --database=trnm \
    <"$migration" 2>&1 | tee "$evidence/logs/migration.log"
  sql_exec() {
    docker exec "$container" cockroach sql --insecure --format=tsv \
      --host=127.0.0.1:26257 --database=trnm --execute="$1" \
      | tail -n +2
  }
fi

sql_exec "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'trnm_%'" \
  | tr -d '[:space:]' >"$evidence/table-count.txt"
test "$(cat "$evidence/table-count.txt")" = 10

cargo build --locked --package trnm-persistence-pg \
  --bin trnm-pg-command --bin trnm-pg-retry \
  2>&1 | tee "$evidence/logs/cargo-build.log"

export TRNM_DATABASE_URL="$database_url"
export TRNM_DATABASE_PROFILE="$profile"
export TRNM_SCHEMA_SOURCE_COMMIT="$commit"
export TRNM_SCHEMA_APPLIED_AT_MS=1

command_bin=target/debug/trnm-pg-command
retry_bin=target/debug/trnm-pg-retry

"$command_bin" bootstrap \
  --entity-byte 17 \
  --authority-generation 1 \
  --state-byte 18 \
  --updated-at-ms 10 \
  | tee "$evidence/bootstrap.json"

"$command_bin" apply \
  --entity-byte 17 \
  --command-byte 33 \
  --fingerprint-byte 34 \
  --expected-revision 0 \
  --authority-generation 1 \
  --state-byte 35 \
  --committed-at-ms 20 \
  | tee "$evidence/applied.json"

"$command_bin" apply \
  --entity-byte 17 \
  --command-byte 33 \
  --fingerprint-byte 34 \
  --expected-revision 0 \
  --authority-generation 1 \
  --state-byte 35 \
  --committed-at-ms 20 \
  | tee "$evidence/duplicate.json"

python3 - "$evidence/applied.json" "$evidence/duplicate.json" <<'PY'
import json
import sys

applied = json.load(open(sys.argv[1], encoding="utf-8"))
duplicate = json.load(open(sys.argv[2], encoding="utf-8"))
assert applied["outcome"] == "applied", applied
assert duplicate["outcome"] == "duplicate", duplicate
for key in ("revision", "first_event_sequence", "last_event_sequence", "event_count", "outbox_count"):
    assert applied[key] == duplicate[key], (key, applied, duplicate)
assert applied["event_count"] == 1 and applied["outbox_count"] == 1
assert applied["compatibility_credit"] is False
assert duplicate["compatibility_credit"] is False
PY

if "$command_bin" apply \
  --entity-byte 17 --command-byte 33 --fingerprint-byte 99 \
  --expected-revision 1 --authority-generation 1 --state-byte 36 --committed-at-ms 30 \
  >"$evidence/changed-fingerprint.out" 2>"$evidence/changed-fingerprint.err"; then
  echo 'changed fingerprint unexpectedly succeeded' >&2
  exit 1
fi
grep -q 'command_id_conflict' "$evidence/changed-fingerprint.err"

if "$command_bin" apply \
  --entity-byte 17 --command-byte 40 --fingerprint-byte 41 \
  --expected-revision 0 --authority-generation 1 --state-byte 42 --committed-at-ms 30 \
  >"$evidence/stale-revision.out" 2>"$evidence/stale-revision.err"; then
  echo 'stale revision unexpectedly succeeded' >&2
  exit 1
fi
grep -q 'entity_revision_mismatch' "$evidence/stale-revision.err"

if "$command_bin" apply \
  --entity-byte 17 --command-byte 40 --fingerprint-byte 41 \
  --expected-revision 1 --authority-generation 2 --state-byte 42 --committed-at-ms 30 \
  >"$evidence/stale-generation.out" 2>"$evidence/stale-generation.err"; then
  echo 'stale generation unexpectedly succeeded' >&2
  exit 1
fi
grep -q 'authority_generation_mismatch' "$evidence/stale-generation.err"

"$retry_bin" apply \
  --entity-byte 17 \
  --command-byte 50 \
  --fingerprint-byte 51 \
  --expected-revision 1 \
  --authority-generation 1 \
  --state-byte 52 \
  --committed-at-ms 40 \
  | tee "$evidence/retry-driver.json"

# The decode query is useful on PostgreSQL but not profile-neutral, so it is
# diagnostic only. The totals below are the required cross-profile invariant.
sql_exec "SELECT count(*) FROM trnm_command_receipts WHERE entity_id=decode(repeat('11',16),'hex')" \
  | tr -d '[:space:]' >"$evidence/receipt-count.txt" || true
sql_exec 'SELECT count(*) FROM trnm_command_receipts' | tr -d '[:space:]' >"$evidence/receipt-total.txt"
sql_exec 'SELECT count(*) FROM trnm_events' | tr -d '[:space:]' >"$evidence/event-total.txt"
sql_exec 'SELECT count(*) FROM trnm_outbox' | tr -d '[:space:]' >"$evidence/outbox-total.txt"
sql_exec 'SELECT revision FROM trnm_entity_heads' | tr -d '[:space:]' >"$evidence/entity-revision.txt"
test "$(cat "$evidence/receipt-total.txt")" = 2
test "$(cat "$evidence/event-total.txt")" = 2
test "$(cat "$evidence/outbox-total.txt")" = 2
test "$(cat "$evidence/entity-revision.txt")" = 2

capture_container_diagnostics

python3 - "$evidence" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
source = {}
for line in (root / "source.txt").read_text().splitlines():
    key, value = line.split("=", 1)
    source[key] = value
artifacts = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.name in {"manifest.json", "SHA256SUMS"}:
        continue
    data = path.read_bytes()
    artifacts.append({
        "path": str(path.relative_to(root)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    })
manifest = {
    "schema": "trillionnium.pgwire-vertical-slice-evidence.v1",
    "target_repository": source["repository"],
    "target_commit": source["commit"],
    "target_tree": source["tree"],
    "profile": source["profile"],
    "image": source["image"],
    "image_identity": (root / "image.txt").read_text().splitlines(),
    "migration": source["migration"],
    "migration_sha256": source["migration_sha256"],
    "run_id": source["run_id"],
    "assertions": {
        "table_count": 10,
        "exact_duplicate_receipt": True,
        "changed_fingerprint_rejected": True,
        "stale_revision_rejected": True,
        "stale_generation_rejected": True,
        "receipt_total": 2,
        "event_total": 2,
        "outbox_total": 2,
        "entity_revision": 2,
    },
    "artifacts": artifacts,
    "claims": {
        "source_vertical_slice_executed": True,
        "serializable_conflict_injected": False,
        "backup_restore_proven": False,
        "pitr_proven": False,
        "ha_proven": False,
        "compatibility_credit": False,
        "production_ready": False,
    },
    "limitations": [
        "No forced SQLSTATE 40001 conflict is injected by this harness.",
        "No process-kill, backup/restore, PITR, failover, load or immutable Nakama differential is performed.",
        "The artifact needs target-native execution and independent database review before gap or gate credit.",
    ],
}
(root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY

find "$evidence" -type f ! -name SHA256SUMS -print0 \
  | sort -z | xargs -0 sha256sum >"$evidence/SHA256SUMS"

echo "PG-wire vertical slice passed: $evidence"
