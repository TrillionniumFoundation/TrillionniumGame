#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
checker="$root/scripts/check-world-command-faultlab-postgres-binding.sh"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/scripts"
cp "$root/compose.yaml" "$tmp/compose.yaml"
cp "$root/scripts/run-world-command-deployed-fault-lab.sh" "$tmp/scripts/"
cp "$root/scripts/run-world-command-deployed-fault-lab-v2.sh" "$tmp/scripts/"

sed -i 's/POSTGRES_DB: trillionnium_nakama/POSTGRES_DB: wrong_database/' "$tmp/compose.yaml"
if "$checker" "$tmp" >/dev/null 2>&1; then
  echo 'PostgreSQL binding checker accepted a mismatched database' >&2
  exit 1
fi

cp "$root/compose.yaml" "$tmp/compose.yaml"
sed -i 's/-U postgres -d trillionnium_nakama/-U postgres -d wrong_database/' "$tmp/scripts/run-world-command-deployed-fault-lab-v2.sh"
if "$checker" "$tmp" >/dev/null 2>&1; then
  echo 'PostgreSQL binding checker accepted a mismatched canonical replacement' >&2
  exit 1
fi

printf '%s\n' 'World command fault-lab PostgreSQL negative fixtures: passed'
