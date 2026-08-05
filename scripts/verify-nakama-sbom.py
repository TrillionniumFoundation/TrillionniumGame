#!/usr/bin/env python3
"""Verify the canonical Nakama runtime SBOM against an immutable source tree."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Any


FRONTEND = "docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89"
BUILDER = "heroiclabs/nakama-pluginbuilder:3.40.0@sha256:0455a119585914341672fc17f3c4195a7a21714ecb85cdf7dacbdc47769aed4c"
RUNTIME = "heroiclabs/nakama:3.40.0@sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd"
BUILDER_DIGEST = BUILDER.rsplit("@sha256:", 1)[1]
RUNTIME_DIGEST = RUNTIME.rsplit("@sha256:", 1)[1]
MODULE_PATH = "/nakama/data/modules/backend.so"
GO_VERSION = "1.26.5"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"cannot read canonical SBOM: {error}")
    if not isinstance(value, dict):
        fail("SBOM root is not an object")
    return value


def require_exact_source(runtime: pathlib.Path) -> None:
    for path in runtime.rglob("*"):
        if path.is_symlink():
            fail(f"runtime source symlink is forbidden: {path.relative_to(runtime)}")
    for name in ("Dockerfile", "go.mod", "go.sum"):
        path = runtime / name
        if not path.is_file() or path.is_symlink():
            fail(f"runtime source requires a regular non-symlink {name}")

    dockerfile = (runtime / "Dockerfile").read_text(encoding="utf-8")
    lines = dockerfile.splitlines()
    if not lines or lines[0] != f"# syntax={FRONTEND}":
        fail("Dockerfile frontend is not the exact pinned frontend")
    from_lines = [
        line.strip()
        for line in lines
        if re.match(r"^\s*FROM(?:\s|$)", line, flags=re.IGNORECASE)
    ]
    if from_lines != [f"FROM {BUILDER} AS builder", f"FROM {RUNTIME}"]:
        fail(f"Dockerfile FROM set/order differs: {from_lines!r}")
    if dockerfile.count("GOTOOLCHAIN=local") < 2:
        fail("Dockerfile does not fail closed to the local Go toolchain")
    if "go version go1.26.5 linux/amd64" not in dockerfile:
        fail("Dockerfile does not assert the exact Go 1.26.5 linux/amd64 toolchain")
    if "COPY --from=builder /release/ /" not in dockerfile:
        fail("Dockerfile no longer copies the normalized release tree")
    if re.search(r"//go:embed\b", "\n".join(
        path.read_text(encoding="utf-8", errors="strict")
        for path in sorted(runtime.rglob("*.go"))
        if path.is_file()
    )):
        fail("runtime source may not embed the tracked SBOM or other build context files")

    go_mod = (runtime / "go.mod").read_text(encoding="utf-8")
    directives = [line.strip() for line in go_mod.splitlines() if line.startswith(("go ", "toolchain "))]
    if directives != [f"go {GO_VERSION}"]:
        fail(f"go.mod toolchain directives differ: {directives!r}")


def verify(sbom_path: pathlib.Path, runtime: pathlib.Path) -> str:
    require_exact_source(runtime)
    sbom = load_json(sbom_path)
    if set(sbom) != {"bomFormat", "components", "dependencies", "metadata", "specVersion", "version"}:
        fail("SBOM top-level field set differs")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != "1.5" or sbom.get("version") != 1:
        fail("SBOM identity differs from canonical CycloneDX 1.5")

    metadata = sbom.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {"component", "properties", "tools"}:
        fail("SBOM metadata field set differs or carries volatile metadata")
    expected_main = {
        "bom-ref": "pkg:golang/github.com%2FTrillionniumFoundation%2FTrillionnium-Nakama%2Fruntime@0.0.0%2Bsource",
        "name": "github.com/TrillionniumFoundation/Trillionnium-Nakama/runtime",
        "purl": "pkg:golang/github.com%2FTrillionniumFoundation%2FTrillionnium-Nakama%2Fruntime@0.0.0%2Bsource",
        "type": "application",
        "version": "0.0.0+source",
    }
    if metadata.get("component") != expected_main:
        fail("SBOM main component differs")
    expected_tools = {
        "components": [
            {
                "bom-ref": f"pkg:golang/go@{GO_VERSION}",
                "name": "go",
                "purl": f"pkg:golang/go@{GO_VERSION}",
                "type": "application",
                "version": GO_VERSION,
            },
            {"name": "trnm-nakama-sbom-generator", "type": "application", "version": "3"},
        ]
    }
    if metadata.get("tools") != expected_tools:
        fail("SBOM build-tool set differs")
    expected_properties = [
        {"name": "trnm:dockerfile-frontend", "value": FRONTEND},
        {"name": "trnm:dockerfile:sha256", "value": f"sha256:{sha256(runtime / 'Dockerfile')}"},
        {"name": "trnm:go-sum:sha256", "value": f"sha256:{sha256(runtime / 'go.sum')}"},
    ]
    if metadata.get("properties") != expected_properties:
        fail("SBOM source-property set or value differs")

    components = sbom.get("components")
    if not isinstance(components, list) or not components:
        fail("SBOM component set is empty")
    if not all(isinstance(component, dict) for component in components):
        fail("SBOM component is not an object")
    refs = [component.get("bom-ref") for component in components]
    if not all(isinstance(ref, str) and ref for ref in refs) or len(refs) != len(set(refs)):
        fail("SBOM component bom-ref values are missing or duplicated")
    if expected_main["bom-ref"] in refs:
        fail("SBOM main component is duplicated in components")

    files = [component for component in components if component.get("type") == "file"]
    if len(files) != 1:
        fail("SBOM must contain exactly one file component")
    module = files[0]
    if set(module) != {"bom-ref", "hashes", "name", "type"}:
        fail("runtime module file component field set differs")
    hashes = module.get("hashes")
    if (
        module.get("bom-ref") != f"file:{MODULE_PATH}"
        or module.get("name") != MODULE_PATH
        or not isinstance(hashes, list)
        or len(hashes) != 1
        or not isinstance(hashes[0], dict)
        or set(hashes[0]) != {"alg", "content"}
        or hashes[0].get("alg") != "SHA-256"
        or not isinstance(hashes[0].get("content"), str)
        or re.fullmatch(r"[0-9a-f]{64}", hashes[0]["content"]) is None
    ):
        fail("runtime module path/hash binding differs")
    module_sha256 = hashes[0]["content"]

    expected_containers = [
        {
            "bom-ref": f"pkg:oci/nakama-pluginbuilder@3.40.0?digest=sha256%3A{BUILDER_DIGEST}",
            "hashes": [{"alg": "SHA-256", "content": BUILDER_DIGEST}],
            "name": "heroiclabs/nakama-pluginbuilder",
            "properties": [{"name": "trnm:image-stage", "value": "builder"}],
            "type": "container",
            "version": "3.40.0",
        },
        {
            "bom-ref": f"pkg:oci/nakama@3.40.0?digest=sha256%3A{RUNTIME_DIGEST}",
            "hashes": [{"alg": "SHA-256", "content": RUNTIME_DIGEST}],
            "name": "heroiclabs/nakama",
            "properties": [{"name": "trnm:image-stage", "value": "runtime"}],
            "type": "container",
            "version": "3.40.0",
        },
    ]
    containers = sorted(
        (component for component in components if component.get("type") == "container"),
        key=lambda component: component["bom-ref"],
    )
    if containers != expected_containers:
        fail("SBOM builder/runtime base set differs")
    allowed_types = {"container", "file", "library"}
    if any(component.get("type") not in allowed_types for component in components):
        fail("SBOM contains an unexpected component type")

    known_refs = {expected_main["bom-ref"], *refs}
    dependencies = sbom.get("dependencies")
    if not isinstance(dependencies, list):
        fail("SBOM dependencies are not an array")
    dependency_refs: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, dict) or set(dependency) != {"dependsOn", "ref"}:
            fail("SBOM dependency field set differs")
        ref = dependency.get("ref")
        depends_on = dependency.get("dependsOn")
        if not isinstance(ref, str) or ref not in known_refs or ref in dependency_refs:
            fail(f"SBOM dependency ref is dangling or duplicated: {ref!r}")
        dependency_refs.add(ref)
        if (
            not isinstance(depends_on, list)
            or depends_on != sorted(set(depends_on))
            or any(not isinstance(item, str) or item not in known_refs for item in depends_on)
        ):
            fail(f"SBOM dependency closure differs for {ref}")
        if "/go@" in ref or "/toolchain@" in ref:
            fail("Go toolchain graph edges must not be runtime package dependencies")

    return module_sha256


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: verify-nakama-sbom.py SBOM RUNTIME_SOURCE")
    sbom = pathlib.Path(sys.argv[1]).resolve(strict=True)
    runtime = pathlib.Path(sys.argv[2]).resolve(strict=True)
    if not runtime.is_dir() or runtime.is_symlink():
        fail("runtime source must be a regular directory")
    print(verify(sbom, runtime))


if __name__ == "__main__":
    main()
