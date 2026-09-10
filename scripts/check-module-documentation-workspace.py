#!/usr/bin/env python3
"""Validate module workspace registration and README workspace semantics."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path("docs/status/MODULE_DOCUMENTATION.json")
ROOT_SENTENCE = (
    "This root-workspace package is explicitly registered in package authority "
    "and must execute in the stable aggregate merge gate."
)
ISOLATED_SENTENCE_PREFIX = "This isolated workspace is explicitly registered"
ROOT_FORBIDDEN_PHRASES = (
    "This isolated workspace is explicitly registered",
    "The isolated workspace must also execute",
    "remaining an isolated mandatory gate target",
)


class ValidationError(RuntimeError):
    """Raised when workspace registration and module documentation diverge."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
        )
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"cannot read {path}: {error}") from error
    require(isinstance(value, dict), f"{path}: top-level object required")
    return value


def canonical_relative(value: Any, label: str) -> str:
    require(isinstance(value, str) and value, f"{label}: non-empty path required")
    require("\\" not in value, f"{label}: backslashes are forbidden")
    path = Path(value)
    require(not path.is_absolute(), f"{label}: absolute path is forbidden")
    require(".." not in path.parts, f"{label}: parent traversal is forbidden")
    require(path.as_posix() == value, f"{label}: canonical path required")
    return value


def workspace_sets(root: Path) -> tuple[set[str], set[str]]:
    try:
        cargo = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValidationError(f"cannot read root Cargo.toml: {error}") from error
    workspace = cargo.get("workspace")
    require(isinstance(workspace, dict), "Cargo.toml: [workspace] is required")
    members_raw = workspace.get("members")
    exclude_raw = workspace.get("exclude", [])
    require(
        isinstance(members_raw, list)
        and members_raw
        and all(isinstance(value, str) for value in members_raw),
        "Cargo.toml: workspace.members must be a non-empty string array",
    )
    require(
        isinstance(exclude_raw, list)
        and all(isinstance(value, str) for value in exclude_raw),
        "Cargo.toml: workspace.exclude must be a string array",
    )
    members = {
        canonical_relative(value, "workspace.members") for value in members_raw
    }
    excluded = {
        canonical_relative(value, "workspace.exclude") for value in exclude_raw
    }
    require(not members.intersection(excluded), "workspace member is also excluded")
    return members, excluded


def validate(root: Path = ROOT) -> dict[str, int | str]:
    registry = load_json(root / REGISTRY)
    require(
        registry.get("schema") == "trillionnium.module-documentation.v1",
        "unexpected module documentation schema",
    )
    rows = registry.get("modules")
    require(isinstance(rows, list) and rows, "module registry must contain modules")
    members, excluded = workspace_sets(root)

    root_count = 0
    isolated_count = 0
    seen: set[str] = set()
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"modules[{index}] must be an object")
        module_id = row.get("id")
        require(
            isinstance(module_id, str)
            and re.fullmatch(r"trnm-[a-z0-9-]+", module_id) is not None,
            f"modules[{index}].id is invalid",
        )
        require(module_id not in seen, f"duplicate module id: {module_id}")
        seen.add(module_id)
        path = canonical_relative(row.get("path"), f"{module_id}.path")
        documentation = canonical_relative(
            row.get("documentation"), f"{module_id}.documentation"
        )
        workspace = row.get("workspace")
        require(workspace in {"root", "isolated"}, f"{module_id}: invalid workspace")
        try:
            text = (root / documentation).read_text(encoding="utf-8")
        except OSError as error:
            raise ValidationError(f"{module_id}: cannot read README: {error}") from error

        marker = f"Workspace class: `{workspace}`"
        require(
            text.count(marker) == 1,
            f"{module_id}: README workspace marker must be exactly {marker!r}",
        )
        other = "isolated" if workspace == "root" else "root"
        require(
            f"Workspace class: `{other}`" not in text,
            f"{module_id}: README contains conflicting workspace marker",
        )

        if workspace == "root":
            root_count += 1
            require(path in members, f"{module_id}: root module missing from workspace.members")
            require(path not in excluded, f"{module_id}: root module is excluded")
            for phrase in ROOT_FORBIDDEN_PHRASES:
                require(
                    phrase not in text,
                    f"{module_id}: root README claims isolated workspace semantics: {phrase}",
                )
        else:
            isolated_count += 1
            require(path in excluded, f"{module_id}: isolated module missing from workspace.exclude")
            require(path not in members, f"{module_id}: isolated module is a root member")
            require(
                ISOLATED_SENTENCE_PREFIX in text,
                f"{module_id}: isolated README lacks explicit aggregate-gate contract",
            )

    require(
        root_count == len(members),
        "module registry root count does not match workspace.members",
    )
    require(
        isolated_count == len(excluded),
        "module registry isolated count does not match workspace.exclude",
    )
    return {
        "status": "verified",
        "module_count": len(rows),
        "root_workspace_count": root_count,
        "isolated_workspace_count": isolated_count,
    }


def main() -> int:
    try:
        print(json.dumps(validate(), sort_keys=True, separators=(",", ":")))
        return 0
    except ValidationError as error:
        print(f"module workspace documentation validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
