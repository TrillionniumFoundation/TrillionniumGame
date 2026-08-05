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
source_tree=$(git rev-parse 'HEAD^{tree}')
source_date_epoch=$(git show -s --format=%ct HEAD)
sbom=runtime/sbom.cdx.json
sbom_sha256=$(sha256sum "$sbom" | cut -d' ' -f1)
runtime_module_path=/nakama/data/modules/backend.so
[[ "$revision" =~ ^[0-9a-f]{40}$ \
  && "$source_tree" =~ ^[0-9a-f]{40}$ \
  && "$source_date_epoch" =~ ^[0-9]+$ \
  && "$sbom_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo 'ERROR: source revision metadata is not canonical' >&2
  exit 1
}
jq -e '
  .bomFormat == "CycloneDX"
  and .specVersion == "1.5"
  and (.components | type == "array" and length > 0)
  and (has("serialNumber") | not)
  and ((.metadata // {}) | has("timestamp") | not)
' "$sbom" >/dev/null
sbom_module_sha256=$(jq -er --arg path "$runtime_module_path" '
  [.components[]
   | select(.type == "file"
       and .["bom-ref"] == ("file:" + $path)
       and .name == $path
       and (.hashes | type == "array" and length == 1)
       and .hashes[0].alg == "SHA-256"
       and (.hashes[0].content | type == "string" and test("^[0-9a-f]{64}$")))] as $matches
  | if ($matches | length) == 1
    then $matches[0].hashes[0].content
    else error("SBOM must contain exactly one canonical runtime module file component")
    end
' "$sbom")

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
  --build-arg "PAPER_RAID_SOURCE_TREE=$source_tree"
  --build-arg "PAPER_RAID_SBOM_SHA256=$sbom_sha256"
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
label_source_tree=$(sudo -n docker image inspect \
  --format '{{index .Config.Labels "org.trillionnium.source.tree"}}' "$image")
label_sbom_sha256=$(sudo -n docker image inspect \
  --format '{{index .Config.Labels "org.trillionnium.sbom.sha256"}}' "$image")
module_sha=$(sudo -n docker run --rm --network none --read-only \
  --entrypoint /bin/sh "$image" -c 'sha256sum /nakama/data/modules/backend.so' \
  | awk '{print $1}')
repro_module_sha=$(sudo -n docker run --rm --network none --read-only \
  --entrypoint /bin/sh "$repro_image" -c 'sha256sum /nakama/data/modules/backend.so' \
  | awk '{print $1}')
image_sbom_sha=$(sudo -n docker run --rm --network none --read-only \
  --entrypoint /bin/sh "$image" \
  -c 'sha256sum /nakama/share/doc/trnm-paper-raid/sbom.cdx.json' \
  | awk '{print $1}')
[[ "$configured_user" == 65532:65532 ]]
[[ "$label_revision" == "$revision" ]]
[[ "$label_source_tree" == "$source_tree" ]]
[[ "$label_sbom_sha256" == "$sbom_sha256" ]]
[[ "$module_sha" =~ ^[0-9a-f]{64}$ \
  && "$module_sha" == "$repro_module_sha" \
  && "$module_sha" == "$sbom_module_sha256" ]] || {
  printf 'ERROR: runtime module differs from reproducible rebuild or tracked/image SBOM: image=%s repro=%s sbom=%s\n' \
    "$module_sha" "$repro_module_sha" "$sbom_module_sha256" >&2
  exit 1
}
[[ "$image_sbom_sha" == "$sbom_sha256" ]]

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

printf 'Nakama immutable image gate: PASS image_id=%s revision=%s tree=%s module_sha256=%s sbom_sha256=%s\n' \
  "$image_id" "$revision" "$source_tree" "$module_sha" "$sbom_sha256"
