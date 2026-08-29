#!/usr/bin/env bash
set -euo pipefail

root=${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}
root_compose="$root/compose.yaml"
runner="$root/scripts/run-world-command-deployed-fault-lab.sh"
entrypoint="$root/scripts/run-world-command-deployed-fault-lab-v2.sh"

for file in "$root_compose" "$runner" "$entrypoint"; do
  [[ -f "$file" ]] || { echo "missing ${file#$root/}" >&2; exit 1; }
done

grep -q 'POSTGRES_DB: trillionnium_nakama' "$root_compose" || {
  echo 'root Compose PostgreSQL database identity drifted' >&2
  exit 1
}
grep -q 'POSTGRES_USER: postgres' "$root_compose" || {
  echo 'root Compose PostgreSQL user identity drifted' >&2
  exit 1
}
grep -q -- '-U nakama -d nakama' "$runner" || {
  echo 'compatibility runner replacement point drifted' >&2
  exit 1
}
grep -q -- '-U postgres -d trillionnium_nakama' "$entrypoint" || {
  echo 'canonical PostgreSQL replacement is absent' >&2
  exit 1
}
grep -q 'source.count(needle) != 1' "$entrypoint" || {
  echo 'replacement must fail closed when the source shape changes' >&2
  exit 1
}

printf '%s\n' 'World command fault-lab PostgreSQL binding: passed'
