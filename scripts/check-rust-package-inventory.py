#!/usr/bin/env python3
"""Fail closed when a first-party Rust package or target escapes the merge gate."""
from __future__ import annotations

import json
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROOT_REAL = ROOT.resolve()
AUTHORITY_PATH = ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json"
ROOT_MANIFEST = ROOT / "Cargo.toml"
MERGE_GATE = ROOT / ".github/workflows/trillionnium-game-merge-gate.yml"


class InventoryError(RuntimeError):
    """Raised when the Rust package authority contract is violated."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def normalized_path(value: str | Path) -> str:
    path = Path(value)
    return path.as_posix().rstrip("/")


def repository_relative(path: Path) -> str:
    resolved = path.resolve()
    require(resolved.is_relative_to(ROOT_REAL), f"target escapes repository: {path}")
    return resolved.relative_to(ROOT_REAL).as_posix()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryError(f"cannot load {repository_relative(path)}: {error}") from error
    require(isinstance(value, dict), f"{repository_relative(path)} must contain an object")
    return value


def load_toml(path: Path) -> dict[str, Any]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise InventoryError(f"cannot load {repository_relative(path)}: {error}") from error
    require(isinstance(value, dict), f"{repository_relative(path)} must contain a table")
    return value


def target_path(
    manifest_dir: Path,
    declared: str,
    *,
    manifest: str,
    target_kind: str,
    target_name: str,
) -> str:
    path = (manifest_dir / declared).resolve()
    require(
        path.is_relative_to(ROOT_REAL),
        f"{manifest}: {target_kind} {target_name!r} escapes the repository: {declared}",
    )
    require(
        path.is_file(),
        f"{manifest}: {target_kind} {target_name!r} points to missing file {declared}",
    )
    return repository_relative(path)


def discover_targets(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    manifest_rel = repository_relative(manifest_path)
    manifest_dir = manifest_path.parent
    package = manifest.get("package")
    require(isinstance(package, dict), f"{manifest_rel}: missing [package]")
    package_name = package.get("name")
    require(isinstance(package_name, str) and package_name, f"{manifest_rel}: invalid package name")

    targets: list[dict[str, str]] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_names: set[tuple[str, str]] = set()

    def add(kind: str, name: str, declared_path: str) -> None:
        source = target_path(
            manifest_dir,
            declared_path,
            manifest=manifest_rel,
            target_kind=kind,
            target_name=name,
        )
        source_key = (kind, source)
        if source_key in seen_sources:
            return
        name_key = (kind, name)
        require(
            name_key not in seen_names,
            f"{manifest_rel}: duplicate {kind} target name {name!r}",
        )
        seen_sources.add(source_key)
        seen_names.add(name_key)
        targets.append({"kind": kind, "name": name, "source": source})

    lib = manifest.get("lib")
    if lib is not None:
        require(isinstance(lib, dict), f"{manifest_rel}: [lib] must be a table")
        lib_name = lib.get("name", package_name.replace("-", "_"))
        lib_path = lib.get("path", "src/lib.rs")
        require(isinstance(lib_name, str) and lib_name, f"{manifest_rel}: invalid lib name")
        require(isinstance(lib_path, str) and lib_path, f"{manifest_rel}: invalid lib path")
        add("lib", lib_name, lib_path)
    elif (manifest_dir / "src/lib.rs").is_file():
        add("lib", package_name.replace("-", "_"), "src/lib.rs")

    explicit_bins = manifest.get("bin", [])
    require(isinstance(explicit_bins, list), f"{manifest_rel}: [[bin]] must be an array")
    for row in explicit_bins:
        require(isinstance(row, dict), f"{manifest_rel}: invalid [[bin]] entry")
        name = row.get("name", package_name)
        require(isinstance(name, str) and name, f"{manifest_rel}: invalid bin name")
        declared = row.get("path")
        if declared is None:
            candidates = [
                "src/main.rs",
                f"src/bin/{name}.rs",
                f"src/bin/{name}/main.rs",
            ]
            existing = [value for value in candidates if (manifest_dir / value).is_file()]
            require(
                len(existing) == 1,
                f"{manifest_rel}: bin {name!r} without path has {len(existing)} candidate sources",
            )
            declared = existing[0]
        require(isinstance(declared, str) and declared, f"{manifest_rel}: invalid bin path")
        add("bin", name, declared)

    if package.get("autobins", True) is not False:
        if (manifest_dir / "src/main.rs").is_file():
            add("bin", package_name, "src/main.rs")
        bin_root = manifest_dir / "src/bin"
        if bin_root.is_dir():
            for source in sorted(bin_root.glob("*.rs")):
                add("bin", source.stem, source.relative_to(manifest_dir).as_posix())
            for source in sorted(bin_root.glob("*/main.rs")):
                add("bin", source.parent.name, source.relative_to(manifest_dir).as_posix())

    require(targets, f"{manifest_rel}: package has no discoverable library or binary target")
    return sorted(targets, key=lambda value: (value["kind"], value["name"], value["source"]))


def verify() -> dict[str, Any]:
    authority = load_json(AUTHORITY_PATH)
    require(
        authority.get("schema") == "trillionnium.rust-package-authority.v1",
        "wrong Rust package authority schema",
    )
    require(authority.get("project_id") == "trillionnium-game", "wrong project_id")
    require(authority.get("plan_version") == 3, "wrong plan version")

    root = load_toml(ROOT_MANIFEST)
    workspace = root.get("workspace")
    require(isinstance(workspace, dict), "root Cargo.toml has no [workspace]")
    actual_members = {normalized_path(value) for value in workspace.get("members", [])}
    actual_excludes = {normalized_path(value) for value in workspace.get("exclude", [])}
    expected_members = {
        normalized_path(value) for value in authority.get("workspace", {}).get("members", [])
    }
    isolated_rows = authority.get("isolated_workspaces", [])
    require(isinstance(isolated_rows, list), "isolated_workspaces must be an array")
    isolated_manifests = {
        normalized_path(row["manifest"])
        for row in isolated_rows
        if isinstance(row, dict) and isinstance(row.get("manifest"), str)
    }
    require(len(isolated_manifests) == len(isolated_rows), "invalid or duplicate isolated manifest")
    isolated_dirs = {normalized_path(Path(value).parent) for value in isolated_manifests}

    require(actual_members == expected_members, "root workspace members drift from authority")
    require(actual_excludes == isolated_dirs, "root workspace excludes drift from isolated authority")
    require(not (actual_members & actual_excludes), "workspace member is also excluded")

    discovered_manifest_paths = sorted(
        path
        for path in (ROOT / "crates").rglob("Cargo.toml")
        if "target" not in path.relative_to(ROOT).parts
    )
    discovered_manifests = {repository_relative(path) for path in discovered_manifest_paths}
    expected_manifests = {
        f"{directory}/Cargo.toml" for directory in actual_members
    } | isolated_manifests
    missing = sorted(expected_manifests - discovered_manifests)
    unclassified = sorted(discovered_manifests - expected_manifests)
    require(not missing, "declared Rust manifests are missing: " + ", ".join(missing))
    require(not unclassified, "unclassified Rust manifests: " + ", ".join(unclassified))

    merge_gate_text = MERGE_GATE.read_text(encoding="utf-8")
    require(
        "python3 scripts/check-rust-package-inventory.py" in merge_gate_text,
        "aggregate merge gate does not execute the Rust package inventory checker",
    )
    for command in authority.get("workspace", {}).get("required_commands", []):
        require(command in merge_gate_text, f"aggregate merge gate missing command: {command}")
    for manifest in sorted(isolated_manifests):
        require(manifest in merge_gate_text, f"aggregate merge gate omits isolated manifest {manifest}")
        require((ROOT / manifest).parent.joinpath("Cargo.lock").is_file(), f"{manifest}: Cargo.lock missing")
    require((ROOT / "Cargo.lock").is_file(), "root Cargo.lock missing")

    packages: list[dict[str, Any]] = []
    package_names: dict[str, list[str]] = defaultdict(list)
    binary_names: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for manifest_path in discovered_manifest_paths:
        manifest_rel = repository_relative(manifest_path)
        config = load_toml(manifest_path)
        package = config.get("package")
        require(isinstance(package, dict), f"{manifest_rel}: missing [package]")
        package_name = package.get("name")
        require(isinstance(package_name, str) and package_name, f"{manifest_rel}: invalid package name")
        directory = normalized_path(manifest_path.parent.relative_to(ROOT))
        isolated = manifest_rel in isolated_manifests
        require(
            (directory in actual_members) != isolated,
            f"{manifest_rel}: must be exactly one of root member or isolated workspace",
        )
        if isolated:
            require("workspace" in config, f"{manifest_rel}: isolated package must declare [workspace]")
        else:
            require("workspace" not in config, f"{manifest_rel}: root member must not declare nested [workspace]")
        targets = discover_targets(manifest_path, config)
        package_names[package_name].append(manifest_rel)
        for target in targets:
            if target["kind"] == "bin":
                binary_names[target["name"]].append((manifest_rel, target["source"]))
        packages.append(
            {
                "manifest": manifest_rel,
                "package": package_name,
                "workspace": "isolated" if isolated else "root",
                "targets": targets,
            }
        )

    allowed_duplicate_names = {
        name: sorted(values)
        for name, values in authority.get("allowed_duplicate_package_names", {}).items()
    }
    actual_duplicates = {
        name: sorted(values) for name, values in package_names.items() if len(values) > 1
    }
    require(
        actual_duplicates == allowed_duplicate_names,
        f"duplicate package-name set drift: expected {allowed_duplicate_names!r}, got {actual_duplicates!r}",
    )

    server = authority.get("server_binary_authority")
    require(isinstance(server, dict), "server_binary_authority must be an object")
    server_name = server.get("name")
    server_manifest = server.get("manifest")
    server_source = server.get("source")
    require(isinstance(server_name, str) and server_name, "invalid server binary name")
    require(isinstance(server_manifest, str) and server_manifest, "invalid server manifest")
    require(isinstance(server_source, str) and server_source, "invalid server source")
    occurrences = binary_names.get(server_name, [])
    require(
        occurrences == [(server_manifest, server_source)],
        f"server binary authority drift: expected {[(server_manifest, server_source)]!r}, got {occurrences!r}",
    )

    return {
        "schema": "trillionnium.rust-package-inventory-report.v1",
        "authority": repository_relative(AUTHORITY_PATH),
        "root_member_count": len(actual_members),
        "isolated_workspace_count": len(isolated_manifests),
        "package_count": len(packages),
        "binary_count": sum(len(values) for values in binary_names.values()),
        "server_binary": {
            "name": server_name,
            "manifest": server_manifest,
            "source": server_source,
        },
        "packages": sorted(packages, key=lambda value: value["manifest"]),
        "claims": {
            "package_inventory_source_checked": True,
            "exact_head_compiled": False,
            "runtime_verified": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }


def main() -> int:
    try:
        report = verify()
    except (InventoryError, OSError, ValueError) as error:
        print(f"Rust package inventory validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
