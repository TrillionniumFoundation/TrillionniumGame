#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"
clean_source_verifier=$root/scripts/verify-nakama-clean-source.py

for command_name in cmp git jq mktemp python3 tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'ERROR: Nakama SBOM gate requires %s\n' "$command_name" >&2
    exit 1
  }
done
revision=$(git rev-parse --verify 'HEAD^{commit}')
source_tree=$(git rev-parse --verify "$revision^{tree}")
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$source_tree" =~ ^[0-9a-f]{40}$ ]] || {
  echo 'ERROR: source revision metadata is not canonical' >&2
  exit 1
}
source_authority=$(python3 "$clean_source_verifier" \
  --repo-dir "$root" \
  --revision "$revision" \
  --tree "$source_tree")

scratch=$(mktemp -d /tmp/trnm-nakama-sbom.XXXXXXXX)
cleanup() {
  case "$scratch" in
    /tmp/trnm-nakama-sbom.*) find "$scratch" -depth -delete ;;
    *) echo 'ERROR: refusing to clean unexpected SBOM-gate scratch path' >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

mkdir -p "$scratch/source"
git archive --format=tar "$revision" \
  runtime \
  scripts/generate-nakama-sbom.sh \
  scripts/verify-nakama-sbom.py \
  | tar -xf - -C "$scratch/source"

archive_runtime=$scratch/source/runtime
archive_generator=$scratch/source/scripts/generate-nakama-sbom.sh
archive_verifier=$scratch/source/scripts/verify-nakama-sbom.py
generated=$scratch/generated.cdx.json
tracked=$archive_runtime/sbom.cdx.json

python3 "$archive_verifier" "$tracked" "$archive_runtime" >/dev/null
bash "$archive_generator" "$generated" "$archive_runtime"
cmp --silent "$generated" "$tracked" || {
  echo 'ERROR: tracked Nakama SBOM differs from the immutable pinned generator' >&2
  diff -u "$tracked" "$generated" | sed -n '1,240p' >&2 || true
  exit 1
}

expect_rejected() {
  local fixture=$1
  local label=$2
  if python3 "$archive_verifier" "$fixture" "$archive_runtime" >/dev/null 2>&1; then
    printf 'ERROR: SBOM verifier accepted %s\n' "$label" >&2
    exit 1
  fi
}

jq '(.metadata.properties[] | select(.name == "trnm:dockerfile:sha256").value) = "sha256:0000000000000000000000000000000000000000000000000000000000000000"' \
  "$tracked" >"$scratch/stale-property.cdx.json"
expect_rejected "$scratch/stale-property.cdx.json" 'a stale Dockerfile property'

jq '.components += [.components[] | select(.type == "file")]' \
  "$tracked" >"$scratch/duplicate-module.cdx.json"
expect_rejected "$scratch/duplicate-module.cdx.json" 'a duplicate runtime module component'

jq '.dependencies += [{"ref":"pkg:golang/dangling.example@v1.0.0","dependsOn":[]}]' \
  "$tracked" >"$scratch/dangling-dependency.cdx.json"
expect_rejected "$scratch/dangling-dependency.cdx.json" 'a dangling dependency ref'

final_source_authority=$(python3 "$clean_source_verifier" \
  --repo-dir "$root" \
  --revision "$revision" \
  --tree "$source_tree")
[[ "$final_source_authority" == "$source_authority" ]] || {
  echo 'ERROR: repository authority changed while the immutable Nakama SBOM gate was running' >&2
  exit 1
}

printf 'Nakama immutable SBOM gate: PASS revision=%s tree=%s\n' \
  "$revision" "$source_tree"
