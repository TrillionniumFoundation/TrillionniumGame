#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
compose="$root/oracle/immutable/compose.yml"
lock="$root/oracle/immutable/oracle-lock.json"
output=${1:-"$root/run/immutable-oracle"}
mkdir -p "$output"

for command in docker curl python3 sha256sum; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 69; }
done
docker compose version >/dev/null

candidate_commit=${TRNM_CANDIDATE_COMMIT:-$(git -C "$root" rev-parse HEAD 2>/dev/null || printf unknown)}
project=${TRNM_ORACLE_PROJECT:-"tg-oracle-${candidate_commit:0:12}-$$"}
work=$(mktemp -d)
env_file="$work/oracle.env"
chmod 700 "$work"

secret() { printf 'trillionnium-oracle-%s-v1' "$1" | sha256sum | cut -d' ' -f1; }
cat >"$env_file" <<EOF
TRNM_ORACLE_PROJECT=$project
TRNM_ORACLE_HTTP_PORT=0
TRNM_ORACLE_DB_PASSWORD=$(secret db)
TRNM_ORACLE_SESSION_KEY=$(secret session)
TRNM_ORACLE_REFRESH_KEY=$(secret refresh)
TRNM_ORACLE_SERVER_KEY=$(secret server)
TRNM_ORACLE_RUNTIME_KEY=$(secret runtime)
TRNM_ORACLE_CONSOLE_KEY=$(secret console-signing)
TRNM_ORACLE_CONSOLE_PASSWORD=$(secret console-password)
EOF
chmod 600 "$env_file"

cleanup() {
  status=$?
  if [[ ${TRNM_KEEP_ORACLE:-0} != 1 ]]; then
    docker compose --env-file "$env_file" -f "$compose" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT INT TERM

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose --env-file "$env_file" -f "$compose" config >"$output/compose.rendered.yml"
rendered_config_sha256="sha256:$(sha256sum "$output/compose.rendered.yml" | cut -d' ' -f1)"
rm "$output/compose.rendered.yml"

docker compose --env-file "$env_file" -f "$compose" pull
docker compose --env-file "$env_file" -f "$compose" up -d --wait

port_line=$(docker compose --env-file "$env_file" -f "$compose" port nakama 7350)
port=${port_line##*:}
[[ $port =~ ^[0-9]+$ ]] || { echo "invalid Nakama host port: $port_line" >&2; exit 1; }
curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:$port/healthcheck" >"$output/health.body"

nakama_container=$(docker compose --env-file "$env_file" -f "$compose" ps -q nakama)
postgres_container=$(docker compose --env-file "$env_file" -f "$compose" ps -q postgres)
[[ -n $nakama_container && -n $postgres_container ]] || { echo "oracle containers are missing" >&2; exit 1; }
nakama_image_id=$(docker inspect --format '{{.Image}}' "$nakama_container")
postgres_image_id=$(docker inspect --format '{{.Image}}' "$postgres_container")
table_count=$(docker compose --env-file "$env_file" -f "$compose" exec -T postgres \
  psql -U postgres -d nakama -Atc "select count(*) from information_schema.tables where table_schema='public';")
[[ $table_count =~ ^[0-9]+$ ]] || { echo "invalid public table count: $table_count" >&2; exit 1; }
completed=$(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 - "$output/runtime-facts.json" <<PY
import json, platform, subprocess, sys
from pathlib import Path
facts = {
  "candidate_commit": ${candidate_commit@Q},
  "oracle_lock_sha256": "sha256:$(sha256sum "$lock" | cut -d' ' -f1)",
  "compose_sha256": "sha256:$(sha256sum "$compose" | cut -d' ' -f1)",
  "rendered_config_sha256": ${rendered_config_sha256@Q},
  "nakama_image_id": ${nakama_image_id@Q},
  "postgres_image_id": ${postgres_image_id@Q},
  "container_runtime": subprocess.check_output(["docker", "version", "--format", "{{.Server.Version}}"], text=True).strip(),
  "kernel": platform.release(),
  "architecture": platform.machine(),
  "health_status": "healthy",
  "database_table_count": ${table_count@Q},
  "started_at_utc": ${started@Q},
  "completed_at_utc": ${completed@Q},
}
Path(sys.argv[1]).write_text(json.dumps(facts, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY

python3 "$root/scripts/oracle/render-immutable-evidence.py" \
  --lock "$lock" \
  --compose "$compose" \
  --facts "$output/runtime-facts.json" \
  --output "$output/evidence.json"

(
  cd "$output"
  sha256sum health.body runtime-facts.json evidence.json >SHA256SUMS
)
printf 'immutable oracle smoke evidence: %s\n' "$output/evidence.json"
