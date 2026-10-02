#!/usr/bin/env python3
"""Verify the complete ordered production-authoritative migration lock."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "migrations/MIGRATION_CHAIN.lock.json"
EXPECTED_PROFILES = {"postgresql", "cockroachdb"}
SCHEMA_VERSION = 2
DIGEST_ALGORITHM = "ordered-path-git-blob-sha256.v1"


class ValidationError(RuntimeError):
    """Raised when migration source identity or inventory drifts."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def load_lock(root: Path = ROOT) -> dict[str, Any]:
    value = json.loads((root / "migrations/MIGRATION_CHAIN.lock.json").read_text(encoding="utf-8"))
    require(isinstance(value, dict), "migration lock must be an object")
    return value


def ordered_chain_digest(entries: list[tuple[str, str]]) -> str:
    """The Rust runner's exact zero-based position/path/NUL/Git-blob identity."""
    chain_hasher = hashlib.sha256()
    for position, (path, blob) in enumerate(entries):
        chain_hasher.update(position.to_bytes(8, "big"))
        chain_hasher.update(path.encode("utf-8"))
        chain_hasher.update(b"\0")
        chain_hasher.update(bytes.fromhex(blob))
    return chain_hasher.hexdigest()


def validate_source_document(
    lock: dict[str, Any],
    read_source: Callable[[str], bytes],
    profile_inventory: dict[str, list[str]],
) -> dict[str, Any]:
    """Validate local or exact-head remote sources using one lock authority."""
    require(lock.get("schema") == "trillionnium.migration-chain-lock.v1", "wrong lock schema")
    require(lock.get("project_id") == "trillionnium-game", "wrong project_id")
    require(type(lock.get("schema_version")) is int and lock["schema_version"] == SCHEMA_VERSION,
            "unexpected schema version")
    profiles = lock.get("profiles")
    require(isinstance(profiles, dict), "profiles must be an object")
    require(set(profiles) == EXPECTED_PROFILES, "migration profile set drifted")

    all_paths: set[str] = set()
    report: dict[str, Any] = {}
    for profile in sorted(profiles):
        row = profiles[profile]
        require(isinstance(row, dict), f"{profile}: profile row must be an object")
        directory = row.get("directory")
        require(directory == f"migrations/{profile}", f"{profile}: unexpected directory")
        actual_sql = profile_inventory.get(profile)
        require(isinstance(actual_sql, list) and actual_sql, f"{profile}: migration inventory missing")
        require(actual_sql == sorted(set(actual_sql)), f"{profile}: migration inventory is duplicate or unordered")
        ordered = row.get("ordered_files")
        require(isinstance(ordered, list) and ordered, f"{profile}: ordered_files required")
        require(len(ordered) == SCHEMA_VERSION, f"{profile}: incomplete schema-version chain")
        listed_paths: list[str] = []
        chain_entries: list[tuple[str, str]] = []
        for position, item in enumerate(ordered):
            require(isinstance(item, dict), f"{profile}: lock item must be an object")
            path_value = item.get("path")
            expected_blob = item.get("git_blob_sha1")
            require(isinstance(path_value, str) and path_value, f"{profile}: path required")
            require(path_value.startswith(f"migrations/{profile}/"), f"{profile}: path escaped profile")
            require(Path(path_value).name.startswith(f"{position + 1:04}_") and path_value.endswith("_up.sql"),
                    f"{profile}: migration version is not contiguous")
            require(path_value not in all_paths, f"duplicate migration path: {path_value}")
            all_paths.add(path_value)
            listed_paths.append(path_value)
            require(Path(path_value).parent.as_posix() == directory, f"{profile}: migration escaped directory")
            require(path_value in actual_sql, f"missing migration: {path_value}")
            data = read_source(path_value)
            actual_blob = git_blob_sha1(data)
            require(actual_blob == expected_blob, f"{path_value}: blob identity drift")
            chain_entries.append((path_value, actual_blob))
        require(listed_paths == actual_sql, f"{profile}: unlisted, missing or unordered SQL migration")
        report[profile] = {
            "file_count": len(listed_paths),
            "ordered_paths": listed_paths,
            "chain_sha256": ordered_chain_digest(chain_entries),
            "digest_algorithm": DIGEST_ALGORITHM,
        }

    rules = lock.get("rules")
    require(isinstance(rules, dict), "rules must be an object")
    for key in (
        "ordered_files_are_complete",
        "unlisted_sql_is_failure",
        "duplicate_path_is_failure",
        "profile_conclusions_are_separate",
        "semantic_change_requires_adr_and_lock_update",
    ):
        require(rules.get(key) is True, f"migration rule {key} must be true")
    require(rules.get("drop_based_production_rollback_allowed") is False, "DROP rollback must remain forbidden")

    return {
        "schema": "trillionnium.migration-chain-lock-validation.v1",
        "schema_version": SCHEMA_VERSION,
        "digest_algorithm": DIGEST_ALGORITHM,
        "profiles": report,
        "source_identity_verified": True,
        "runtime_execution_verified": False,
        "compatibility_credit": False,
    }


def validate(root: Path = ROOT) -> dict[str, Any]:
    inventory: dict[str, list[str]] = {}
    for profile in EXPECTED_PROFILES:
        directory = root / "migrations" / profile
        require(directory.is_dir(), f"{profile}: migration directory missing")
        inventory[profile] = sorted(
            path.relative_to(root).as_posix()
            for path in directory.rglob("*.sql") if path.is_file()
        )
    return validate_source_document(load_lock(root), lambda path: (root / path).read_bytes(), inventory)


def main() -> int:
    try:
        result = validate()
    except (OSError, ValueError, json.JSONDecodeError, ValidationError) as error:
        print(f"migration lock validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
