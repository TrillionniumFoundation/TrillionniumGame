#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$root"

image=trnm-paper-raid-nakama:local-gate
repro_image=trnm-paper-raid-nakama:reproducibility-gate-$$
scratch=$(mktemp -d)
buildx_version=v0.36.1
buildx_url=https://github.com/docker/buildx/releases/download/v0.36.1/buildx-v0.36.1.linux-amd64
buildx_sha256=48af8a397ebd60178778bf63611dbcebe5f5e7a9be90eb9147b24b9587455778
docker_config=$scratch/docker
buildx_plugin=$docker_config/cli-plugins/docker-buildx

cleanup() {
  sudo -n docker image rm -f "$repro_image" >/dev/null 2>&1 || true
  case "$scratch" in
    /tmp/tmp.*) sudo -n rm -rf -- "$scratch" ;;
    *) echo "ERROR: refusing to remove unexpected image-gate scratch path" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

for command_name in curl docker rg sha256sum sudo; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'ERROR: Nakama image gate requires %s\n' "$command_name" >&2
    exit 1
  }
done
sudo -n docker info >/dev/null
if [[ -n $(git status --porcelain=v1 --untracked-files=all) ]]; then
  echo 'ERROR: immutable Nakama image gate requires a clean committed worktree' >&2
  exit 1
fi

revision=$(git rev-parse HEAD)
source_date_epoch=$(git show -s --format=%ct HEAD)
[[ "$revision" =~ ^[0-9a-f]{40}$ && "$source_date_epoch" =~ ^[0-9]+$ ]] || {
  echo 'ERROR: source revision metadata is not canonical' >&2
  exit 1
}

mkdir -p "$(dirname "$buildx_plugin")"
curl --fail --location --proto '=https' --retry 5 --retry-all-errors \
  --retry-delay 2 --connect-timeout 15 --max-time 300 \
  --show-error --silent --tlsv1.2 \
  "$buildx_url" --output "$buildx_plugin"
actual_buildx_sha256=$(sha256sum "$buildx_plugin" | cut -d' ' -f1)
[[ "$actual_buildx_sha256" == "$buildx_sha256" ]] || {
  echo 'ERROR: disposable buildx checksum differs' >&2
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
  --file runtime/Dockerfile
)
"${docker_cli[@]}" buildx build "${build_args[@]}" --tag "$image" runtime
"${docker_cli[@]}" buildx build "${build_args[@]}" --tag "$repro_image" runtime

image_id=$(sudo -n docker image inspect --format '{{.Id}}' "$image")
repro_image_id=$(sudo -n docker image inspect --format '{{.Id}}' "$repro_image")
[[ "$image_id" == "$repro_image_id" ]] || {
  printf 'ERROR: independent Nakama rebuild differs: %s != %s\n' \
    "$image_id" "$repro_image_id" >&2
  exit 1
}

configured_user=$(sudo -n docker image inspect --format '{{.Config.User}}' "$image")
label_revision=$(sudo -n docker image inspect \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
module_sha=$(sudo -n docker run --rm --network none --read-only \
  --entrypoint /bin/sh "$image" -c 'sha256sum /nakama/data/modules/backend.so' \
  | awk '{print $1}')
repro_module_sha=$(sudo -n docker run --rm --network none --read-only \
  --entrypoint /bin/sh "$repro_image" -c 'sha256sum /nakama/data/modules/backend.so' \
  | awk '{print $1}')
[[ "$configured_user" == 65532:65532 ]]
[[ "$label_revision" == "$revision" ]]
[[ "$module_sha" =~ ^[0-9a-f]{64}$ && "$module_sha" == "$repro_module_sha" ]]

if sudo -n docker image inspect --format '{{json .Config.Env}}' "$image" \
  | rg -n 'TRNM_(HEPTA|NAKAMA)|NAKAMA_(CONSOLE|RUNTIME|SESSION|SOCKET)'; then
  echo 'ERROR: Nakama runtime image contains deployment configuration' >&2
  exit 1
fi
if sudo -n docker history --no-trunc --format '{{.CreatedBy}}' "$image" \
  | rg -n '(PASSWORD|PRIVATE|SECRET|TOKEN|replace-with|change-me)'; then
  echo 'ERROR: Nakama image history contains deployment configuration' >&2
  exit 1
fi

printf 'Nakama immutable image gate: PASS image_id=%s revision=%s module_sha256=%s\n' \
  "$image_id" "$revision" "$module_sha"
