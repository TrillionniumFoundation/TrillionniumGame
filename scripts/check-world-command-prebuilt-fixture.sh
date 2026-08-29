#!/usr/bin/env bash
set -euo pipefail

root=${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}
runner="$root/scripts/run-world-command-deployed-fault-lab-v3.sh"
compose="$root/deploy/world-command-fault-lab/compose.yaml"

fail() { printf 'prebuilt World fixture contract failed: %s\n' "$*" >&2; exit 1; }
[[ -f "$runner" ]] || fail 'v3 runner is missing'
[[ -f "$compose" ]] || fail 'fault-lab Compose overlay is missing'

grep -q 'TRNM_WORLD_FIXTURE_PREBUILT' "$runner" || fail 'prebuilt mode flag is missing'
grep -q 'TRNM_WORLD_FIXTURE_IMAGE' "$runner" || fail 'exact image identity is not required'
grep -q 'docker image inspect' "$runner" || fail 'prebuilt image is not verified locally'
grep -q 'source.count(build_needle) != 1' "$runner" || fail 'build rewrite does not fail closed on source drift'
grep -q '"$compose" build nakama tls-fixture response-drop-proxy' "$runner" || fail 'prebuilt mode still rebuilds the World fixture'
grep -q 'image: ${TRNM_WORLD_FIXTURE_IMAGE' "$compose" || fail 'Compose does not accept the injected World image'

printf '%s\n' 'prebuilt World fixture injection contract: passed'
