#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
bash scripts/project-preflight.sh --dev

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "ERROR: Docker access is required for the pinned Go 1.26.5 restart gate" >&2
  exit 1
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
pattern='^Test(Restart|Resume|Snapshot|Recovery|Persistence|StoredMatch|Corrupt|Truncat)'
image='heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c'
"${docker_cmd[@]}" run --rm --read-only --entrypoint go \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  -e GOCACHE=/tmp/go-build -e GOMODCACHE=/tmp/go-mod -e GOTOOLCHAIN=local \
  -v "$root/runtime:/backend:ro" -w /backend "$image" \
  test -mod=readonly -list "$pattern" ./... >"$tmp"
if ! rg "$pattern" "$tmp" >/dev/null; then
  echo "ERROR: no restart/recovery tests match $pattern" >&2
  sed -n '1,160p' "$tmp" >&2
  exit 1
fi
"${docker_cmd[@]}" run --rm --read-only --entrypoint go \
  --tmpfs /tmp:rw,exec,nosuid,nodev \
  -e GOCACHE=/tmp/go-build -e GOMODCACHE=/tmp/go-mod -e GOTOOLCHAIN=local \
  -v "$root/runtime:/backend:ro" -w /backend "$image" \
  test -count=1 -mod=readonly -run "$pattern" ./...
