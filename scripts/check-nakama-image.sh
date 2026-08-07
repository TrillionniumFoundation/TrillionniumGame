#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

image=trnm-paper-raid-nakama:local-gate
repro_image=trnm-paper-raid-nakama:reproducibility-gate-$$
sentinel_image=trnm-paper-raid-nakama:sentinel-gate-$$
primary_container=trnm-nakama-primary-extract-$$
repro_container=trnm-nakama-repro-extract-$$
scan_container=trnm-nakama-rootfs-scan-$$
sentinel_seed_container=trnm-nakama-sentinel-seed-$$
sentinel_scan_container=trnm-nakama-sentinel-scan-$$
scratch=$(mktemp -d /tmp/trnm-nakama-image.XXXXXXXX)
buildx_version=v0.36.1
buildx_url=https://github.com/docker/buildx/releases/download/v0.36.1/buildx-v0.36.1.linux-amd64
buildx_sha256=48af8a397ebd60178778bf63611dbcebe5f5e7a9be90eb9147b24b9587455778
docker_config=$scratch/docker
buildx_plugin=$docker_config/cli-plugins/docker-buildx
builder_image=heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c
clean_source_verifier=$root/scripts/verify-nakama-clean-source.py

cleanup() {
  sudo -n docker rm -f \
    "$primary_container" "$repro_container" "$scan_container" \
    "$sentinel_seed_container" "$sentinel_scan_container" \
    >/dev/null 2>&1 || true
  sudo -n docker image rm -f "$repro_image" "$sentinel_image" >/dev/null 2>&1 || true
  case "$scratch" in
    /tmp/trnm-nakama-image.*) sudo -n find "$scratch" -depth -delete ;;
    *) echo 'ERROR: refusing to remove unexpected image-gate scratch path' >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

for command_name in cmp curl docker find git jq mktemp python3 rg sha256sum sort stat sudo tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'ERROR: Nakama image gate requires %s\n' "$command_name" >&2
    exit 1
  }
done
revision=$(git rev-parse --verify 'HEAD^{commit}')
source_tree=$(git rev-parse --verify "$revision^{tree}")
source_date_epoch=$(git show -s --format=%ct "$revision")
[[ "$revision" =~ ^[0-9a-f]{40}$ \
  && "$source_tree" =~ ^[0-9a-f]{40}$ \
  && "$source_date_epoch" =~ ^[0-9]+$ ]] || {
  echo 'ERROR: source revision metadata is not canonical' >&2
  exit 1
}
source_authority=$(python3 "$clean_source_verifier" \
  --repo-dir "$root" \
  --revision "$revision" \
  --tree "$source_tree")
jq -e \
  --arg revision "$revision" \
  --arg tree "$source_tree" \
  '.revision == $revision and .tree == $tree and (.tracked_files | type == "number" and . > 0)' \
  <<<"$source_authority" >/dev/null
sudo -n docker info >/dev/null

# Both independent builds consume an archive of the captured commit, never the
# mutable worktree. The final check below additionally rejects concurrent HEAD
# or worktree changes while the gate is running.
mkdir -p "$scratch/source"
git archive --format=tar "$revision" runtime scripts/verify-nakama-sbom.py \
  | tar -xf - -C "$scratch/source"
runtime_context=$scratch/source/runtime
verifier=$scratch/source/scripts/verify-nakama-sbom.py
sbom=$runtime_context/sbom.cdx.json
sbom_sha256=$(sha256sum "$sbom" | cut -d' ' -f1)
runtime_module_path=/nakama/data/modules/backend.so
sbom_module_sha256=$(python3 "$verifier" "$sbom" "$runtime_context")
[[ "$sbom_sha256" =~ ^[0-9a-f]{64}$ && "$sbom_module_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo 'ERROR: immutable SBOM metadata is not canonical' >&2
  exit 1
}
cmp --silent runtime/sbom.cdx.json "$sbom" || {
  echo 'ERROR: worktree SBOM differs from the captured commit' >&2
  exit 1
}

# A pinned builder is not sufficient if the Go command may auto-download a
# different toolchain. The Dockerfile and this runtime probe both fail closed.
toolchain_version=$(sudo -n docker run --rm --pull never --platform linux/amd64 \
  --network none --read-only --env GOTOOLCHAIN=local \
  --entrypoint go "$builder_image" version)
[[ "$toolchain_version" == 'go version go1.26.5 linux/amd64' ]] || {
  printf 'ERROR: pinned Nakama builder toolchain differs: %s\n' "$toolchain_version" >&2
  exit 1
}

mkdir -p "$(dirname "$buildx_plugin")"
curl --fail --location --proto '=https' --proto-redir '=https' \
  --retry 5 --retry-all-errors --retry-delay 2 \
  --connect-timeout 15 --max-time 300 --show-error --silent --tlsv1.2 \
  "$buildx_url" --output "$buildx_plugin"
actual_buildx_sha256=$(sha256sum "$buildx_plugin" | cut -d' ' -f1)
[[ "$actual_buildx_sha256" == "$buildx_sha256" ]] || {
  echo 'ERROR: disposable Buildx checksum differs' >&2
  exit 1
}
chmod 0500 "$buildx_plugin"
docker_cli=(sudo -n env "DOCKER_CONFIG=$docker_config" docker)
"${docker_cli[@]}" buildx version | rg -q --fixed-strings "$buildx_version"

build_args=(
  --load
  --no-cache
  --provenance=false
  --sbom=false
  --platform linux/amd64
  --build-arg "SOURCE_DATE_EPOCH=$source_date_epoch"
  --build-arg BUILDKIT_MULTI_PLATFORM=1
  --build-arg "PAPER_RAID_SOURCE_REVISION=$revision"
  --build-arg "PAPER_RAID_SOURCE_TREE=$source_tree"
  --build-arg "PAPER_RAID_SBOM_SHA256=$sbom_sha256"
  --file "$runtime_context/Dockerfile"
)
"${docker_cli[@]}" buildx build "${build_args[@]}" \
  --iidfile "$scratch/primary.iid" --tag "$image" "$runtime_context"
"${docker_cli[@]}" buildx build "${build_args[@]}" \
  --iidfile "$scratch/repro.iid" --tag "$repro_image" "$runtime_context"

image_id=$(<"$scratch/primary.iid")
repro_image_id=$(<"$scratch/repro.iid")
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ && "$repro_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo 'ERROR: Buildx IID output is not a canonical image config digest' >&2
  exit 1
}
[[ "$image_id" == "$repro_image_id" ]] || {
  printf 'ERROR: independent Nakama rebuild differs: %s != %s\n' \
    "$image_id" "$repro_image_id" >&2
  exit 1
}
[[ $(sudo -n docker image inspect --format '{{.Id}}' "$image") == "$image_id" \
  && $(sudo -n docker image inspect --format '{{.Id}}' "$repro_image") == "$repro_image_id" ]] || {
  echo 'ERROR: mutable build tag no longer resolves to its captured image ID' >&2
  exit 1
}

inspect_json=$(sudo -n docker image inspect "$image_id")
jq -e \
  --arg image_id "$image_id" \
  --arg revision "$revision" \
  --arg source_tree "$source_tree" \
  --arg sbom_sha256 "$sbom_sha256" '
  length == 1
  and .[0].Id == $image_id
  and .[0].Architecture == "amd64"
  and .[0].Os == "linux"
  and .[0].Config.User == "65532:65532"
  and .[0].Config.Labels["org.opencontainers.image.source"] == "https://github.com/TrillionniumFoundation/Trillionnium-Nakama"
  and .[0].Config.Labels["org.opencontainers.image.revision"] == $revision
  and .[0].Config.Labels["org.opencontainers.image.title"] == "Trillionnium Nakama Paper Raid Runtime"
  and .[0].Config.Labels["org.opencontainers.image.description"] == "Nakama authoritative runtime for signed Paper Raid research sessions"
  and .[0].Config.Labels["org.opencontainers.image.sbom"] == "/nakama/share/doc/trnm-paper-raid/sbom.cdx.json"
  and .[0].Config.Labels["org.trillionnium.source.tree"] == $source_tree
  and .[0].Config.Labels["org.trillionnium.sbom.sha256"] == $sbom_sha256
' <<<"$inspect_json" >/dev/null

extract_artifacts() {
  local candidate=$1
  local container=$2
  local destination=$3
  mkdir -p "$destination"
  sudo -n docker create --name "$container" --network none "$candidate" >/dev/null
  sudo -n docker cp "$container:$runtime_module_path" "$destination/backend.so"
  sudo -n docker cp \
    "$container:/nakama/share/doc/trnm-paper-raid/sbom.cdx.json" \
    "$destination/sbom.cdx.json"
  sudo -n docker rm "$container" >/dev/null
}

extract_artifacts "$image_id" "$primary_container" "$scratch/primary"
extract_artifacts "$repro_image_id" "$repro_container" "$scratch/repro"
module_sha=$(sha256sum "$scratch/primary/backend.so" | cut -d' ' -f1)
repro_module_sha=$(sha256sum "$scratch/repro/backend.so" | cut -d' ' -f1)
image_sbom_sha=$(sha256sum "$scratch/primary/sbom.cdx.json" | cut -d' ' -f1)
repro_sbom_sha=$(sha256sum "$scratch/repro/sbom.cdx.json" | cut -d' ' -f1)
[[ "$module_sha" == "$repro_module_sha" \
  && "$module_sha" == "$sbom_module_sha256" \
  && "$image_sbom_sha" == "$repro_sbom_sha" \
  && "$image_sbom_sha" == "$sbom_sha256" ]] || {
  printf 'ERROR: extracted runtime/SBOM differs: module=%s repro_module=%s tracked_module=%s image_sbom=%s repro_sbom=%s tracked_sbom=%s\n' \
    "$module_sha" "$repro_module_sha" "$sbom_module_sha256" \
    "$image_sbom_sha" "$repro_sbom_sha" "$sbom_sha256" >&2
  exit 1
}
cmp --silent "$scratch/primary/backend.so" "$scratch/repro/backend.so"
cmp --silent "$scratch/primary/sbom.cdx.json" "$scratch/repro/sbom.cdx.json"
cmp --silent "$scratch/primary/sbom.cdx.json" "$sbom"

scan_image() {
  local candidate=$1
  local container=$2
  local label=$3
  local scan_root=$scratch/$label
  mkdir -p "$scan_root/rootfs"
  sudo -n docker create --name "$container" --network none "$candidate" >/dev/null
  sudo -n docker export "$container" --output "$scan_root/rootfs.tar"
  sudo -n tar -tf "$scan_root/rootfs.tar" \
    | sed 's#^\./##' >"$scan_root/rootfs.list"
  if rg -n '(^|/)(\.env(\.[^/]*)?|id_(rsa|ed25519)|paper-raid[^/]*(credential|identity|private|secret|token)[^/]*)$' \
    "$scan_root/rootfs.list"; then
    echo 'ERROR: Nakama image contains a forbidden secret/config filename' >&2
    return 1
  fi
  sudo -n tar -xf "$scan_root/rootfs.tar" -C "$scan_root/rootfs"
  sudo -n find "$scan_root/rootfs/nakama/data/modules" -mindepth 1 \
    \( -type f -o -type l \) -printf '%P\n' | sort >"$scan_root/modules.list"
  if [[ $(<"$scan_root/modules.list") != backend.so ]]; then
    echo 'ERROR: Nakama image runtime module set differs from backend.so' >&2
    return 1
  fi
  sudo -n docker rm "$container" >/dev/null
}

scan_image "$image_id" "$scan_container" primary-rootfs

# Prove that the filename scanner is live instead of merely producing a clean
# report for the expected image.
printf '%s\n' 'paper-raid secret sentinel' >"$scratch/paper-raid-private-token"
sudo -n docker create --name "$sentinel_seed_container" --network none "$image_id" >/dev/null
sudo -n docker cp "$scratch/paper-raid-private-token" \
  "$sentinel_seed_container:/nakama/share/doc/trnm-paper-raid/paper-raid-private-token"
sudo -n docker commit --pause=false "$sentinel_seed_container" "$sentinel_image" >/dev/null
sudo -n docker rm "$sentinel_seed_container" >/dev/null
if scan_image "$sentinel_image" "$sentinel_scan_container" sentinel-rootfs; then
  echo 'ERROR: Nakama sensitive-file scanner accepted the sentinel image' >&2
  exit 1
fi

if sudo -n docker image inspect --format '{{json .Config.Env}}' "$image_id" \
  | rg -n 'TRNM_(HEPTA|NAKAMA)|NAKAMA_(CONSOLE|RUNTIME|SESSION|SOCKET)'; then
  echo 'ERROR: Nakama runtime image contains deployment configuration' >&2
  exit 1
fi
if sudo -n docker history --no-trunc --format '{{.CreatedBy}}' "$image_id" \
  | rg -n '(PASSWORD|PRIVATE|SECRET|TOKEN|replace-with|change-me)'; then
  echo 'ERROR: Nakama image history contains deployment configuration' >&2
  exit 1
fi

final_source_authority=$(python3 "$clean_source_verifier" \
  --repo-dir "$root" \
  --revision "$revision" \
  --tree "$source_tree")
[[ "$final_source_authority" == "$source_authority" ]] || {
  echo 'ERROR: repository authority changed while the immutable Nakama image gate was running' >&2
  exit 1
}
[[ $(sudo -n docker image inspect --format '{{.Id}}' "$image") == "$image_id" ]] || {
  echo 'ERROR: canonical Nakama gate tag drifted after verification' >&2
  exit 1
}

printf 'Nakama immutable image gate: PASS image_id=%s revision=%s tree=%s module_sha256=%s sbom_sha256=%s\n' \
  "$image_id" "$revision" "$source_tree" "$module_sha" "$sbom_sha256"
