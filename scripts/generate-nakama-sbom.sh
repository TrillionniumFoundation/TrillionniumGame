#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
output=${1:-}
[[ -n "$output" ]] || {
  echo "usage: $0 OUTPUT" >&2
  exit 64
}

scratch=$(mktemp -d)
cleanup() {
  case "$scratch" in
    /tmp/tmp.*) find "$scratch" -depth -delete ;;
    *) echo "refusing to clean unexpected SBOM scratch path: $scratch" >&2 ;;
  esac
}
trap cleanup EXIT INT TERM

mkdir -p "$scratch/module"

docker_cmd=()
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "Docker access is required for the pinned Go SBOM generator" >&2
  exit 1
fi
builder_image=heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c
"${docker_cmd[@]}" run --rm --pull never --network bridge --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev \
  --volume "$root/runtime:/src:ro" --workdir /src \
  --volume "$scratch/module:/out:rw" \
  --env GOCACHE=/tmp/go-build --env GOPATH=/tmp/go \
  --entrypoint /bin/sh "$builder_image" \
  -ec 'go list -mod=readonly -m -json all; go build --trimpath -mod=readonly -buildmode=plugin -o /out/backend.so .' \
  | jq -s . >"$scratch/modules.json"
"${docker_cmd[@]}" run --rm --pull never --network bridge --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev \
  --volume "$root/runtime:/src:ro" --workdir /src \
  --env GOCACHE=/tmp/go-build --env GOPATH=/tmp/go \
  --entrypoint /bin/sh "$builder_image" \
  -ec 'go mod graph' >"$scratch/graph.txt"

dockerfile_sha256=$(sha256sum "$root/runtime/Dockerfile" | awk '{print $1}')
go_sum_sha256=$(sha256sum "$root/runtime/go.sum" | awk '{print $1}')
runtime_module_path=/nakama/data/modules/backend.so
runtime_module_sha256=$(sha256sum "$scratch/module/backend.so" | awk '{print $1}')
[[ "$runtime_module_sha256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "deterministic runtime module SHA-256 is invalid" >&2
  exit 1
}
runtime_base=sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd
builder_base=sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c

jq -nS \
  --slurpfile modules "$scratch/modules.json" \
  --rawfile graph "$scratch/graph.txt" \
  --arg dockerfile_sha256 "$dockerfile_sha256" \
  --arg go_sum_sha256 "$go_sum_sha256" \
  --arg runtime_module_path "$runtime_module_path" \
  --arg runtime_module_sha256 "$runtime_module_sha256" \
  --arg runtime_base "$runtime_base" \
  --arg builder_base "$builder_base" '
  def version($module):
    if ($module.Version // "") == "" then "0.0.0+source" else $module.Version end;
  def purl($module):
    "pkg:golang/\($module.Path | @uri)@\(version($module) | @uri)";
  def component($module):
    {
      type: (if $module.Main then "application" else "library" end),
      "bom-ref": purl($module),
      name: $module.Path,
      version: version($module),
      purl: purl($module)
    }
    + (if ($module.Sum // "") == "" then {} else {
        properties: [{name: "trnm:go-module-sum", value: $module.Sum}]
      } end);
  ($modules[0]) as $all
  | (first($all[] | select(.Main))) as $main
  | ($all | map({key: (.Path + "@" + version(.)), value: purl(.)}) | from_entries) as $refs
  | ($graph
      | split("\n")
      | map(select(length > 0) | split(" "))
      | group_by(.[0])
      | map({
          ref: ($refs[.[0][0]] // (.[0][0] | sub("@.*$"; "") as $path | "pkg:golang/\($path | @uri)@0.0.0+source")),
          dependsOn: ([.[][1] | $refs[.] // empty] | unique | sort)
        })) as $dependencies
  | {
      bomFormat: "CycloneDX",
      specVersion: "1.5",
      version: 1,
      metadata: {
        tools: {components: [{type: "application", name: "trnm-nakama-sbom-generator", version: "2"}]},
        component: component($main),
        properties: [
          {name: "trnm:dockerfile:sha256", value: ("sha256:" + $dockerfile_sha256)},
          {name: "trnm:go-sum:sha256", value: ("sha256:" + $go_sum_sha256)}
        ]
      },
      components: (
        [$all[] | select(.Main | not) | component(.)]
        + [
          {
            type: "file",
            "bom-ref": ("file:" + $runtime_module_path),
            name: $runtime_module_path,
            hashes: [{alg: "SHA-256", content: $runtime_module_sha256}]
          },
          {
            type: "container",
            "bom-ref": ("pkg:oci/nakama@3.40.0?digest=" + ($runtime_base | @uri)),
            name: "heroiclabs/nakama",
            version: "3.40.0",
            hashes: [{alg: "SHA-256", content: ($runtime_base | sub("^sha256:"; ""))}],
            properties: [{name: "trnm:image-stage", value: "runtime"}]
          },
          {
            type: "container",
            "bom-ref": ("pkg:oci/nakama-pluginbuilder@3.40.0?digest=" + ($builder_base | @uri)),
            name: "heroiclabs/nakama-pluginbuilder",
            version: "3.40.0",
            hashes: [{alg: "SHA-256", content: ($builder_base | sub("^sha256:"; ""))}],
            properties: [{name: "trnm:image-stage", value: "builder"}]
          }
        ]
        | sort_by(."bom-ref")
      ),
      dependencies: ($dependencies | sort_by(.ref))
    }
' >"$scratch/sbom.cdx.json"

jq -e \
  --arg runtime_module_path "$runtime_module_path" \
  --arg runtime_module_sha256 "$runtime_module_sha256" '
  [.components[]
   | select(.type == "file"
       and .["bom-ref"] == ("file:" + $runtime_module_path)
       and .name == $runtime_module_path
       and .hashes == [{"alg":"SHA-256","content":$runtime_module_sha256}])]
  | length == 1
' "$scratch/sbom.cdx.json" >/dev/null || {
  echo "generated SBOM did not bind the deterministic runtime module" >&2
  exit 1
}

mkdir -p "$(dirname "$output")"
install -m 0644 "$scratch/sbom.cdx.json" "$output"
