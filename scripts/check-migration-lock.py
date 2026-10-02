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
SCHEMA_VERSION = 3
DIGEST_ALGORITHM = "ordered-path-git-blob-sha256.v1"
FROZEN_BASE = "326e670cb008a990247e31a63c0c4b0e338df62f"
# Adding a revision must never rewrite either reviewed historical revision,
# even if an edited lock also advertises the new blob.
FROZEN_HISTORICAL_BLOBS = {
    "migrations/postgresql/0001_foundation_up.sql": "07f5f4923d884cc63bf53074096b8d1e04215096",
    "migrations/postgresql/0002_storage_timestamps_up.sql": "e36cc2d743eb863af93774a24615f3c5ec1c3443",
    "migrations/cockroachdb/0001_foundation_up.sql": "b836b8a2f025ef22525e9e5f089db01ab5f06fe6",
    "migrations/cockroachdb/0002_storage_timestamps_up.sql": "700cdb460b9211370777928b980b10e37ae21ae9",
}


def reviewed_actions(profile: str, revision: int) -> tuple[tuple[str, str], ...]:
    """Closed runner declarations, not a general SQL parser or SQL executor."""
    if revision == 2:
        text = "TEXT" if profile == "postgresql" else "STRING"
        integer = "BIGINT" if profile == "postgresql" else "INT8"
        return (
            ("metadata_chain_digest", f"ALTER TABLE trnm_schema_metadata ADD COLUMN chain_digest {text};"),
            ("metadata_digest_algorithm", f"ALTER TABLE trnm_schema_metadata ADD COLUMN digest_algorithm {text};"),
            ("metadata_storage_writer_epoch", f"ALTER TABLE trnm_schema_metadata ADD COLUMN storage_writer_epoch {integer};"),
            ("metadata_upgrade_source_commit", f"ALTER TABLE trnm_schema_metadata ADD COLUMN upgrade_source_commit {text};"),
            ("storage_create_time", "ALTER TABLE trnm_storage_objects ADD COLUMN create_time TIMESTAMPTZ;"),
            ("storage_update_time", "ALTER TABLE trnm_storage_objects ADD COLUMN update_time TIMESTAMPTZ;"),
        )
    require(revision == 3, "unknown action revision")
    return (
        ("metadata_v2_apply_source_commit", "ALTER TABLE trnm_schema_metadata ADD COLUMN v2_apply_source_commit TEXT;"),
        ("storage_value_jsonb", "ALTER TABLE trnm_storage_objects ADD COLUMN value_jsonb JSONB;"),
        ("storage_public_version", "ALTER TABLE trnm_storage_objects ADD COLUMN public_version VARCHAR(32);"),
        ("storage_value_projection_digest", "ALTER TABLE trnm_storage_objects ADD COLUMN value_projection_digest BYTEA;"),
        ("storage_value_origin", "ALTER TABLE trnm_storage_objects ADD COLUMN value_origin TEXT;"),
        ("storage_source_manifest_digest", "ALTER TABLE trnm_storage_objects ADD COLUMN source_manifest_digest BYTEA;"),
        ("storage_native_backfill", "-- trnm:backfill storage_jsonb_v3"),
        ("storage_value_bytes_optional", "ALTER TABLE trnm_storage_objects ALTER COLUMN value_bytes DROP NOT NULL;"),
        ("storage_version_digest_optional", "ALTER TABLE trnm_storage_objects ALTER COLUMN version_digest DROP NOT NULL;"),
        ("storage_value_jsonb_required", "ALTER TABLE trnm_storage_objects ALTER COLUMN value_jsonb SET NOT NULL;"),
        ("storage_public_version_required", "ALTER TABLE trnm_storage_objects ALTER COLUMN public_version SET NOT NULL;"),
        ("storage_value_projection_digest_required", "ALTER TABLE trnm_storage_objects ALTER COLUMN value_projection_digest SET NOT NULL;"),
        ("storage_value_origin_required", "ALTER TABLE trnm_storage_objects ALTER COLUMN value_origin SET NOT NULL;"),
        ("storage_projection_digest", "ALTER TABLE trnm_storage_objects ADD CONSTRAINT storage_projection_digest CHECK (octet_length(value_projection_digest) = 32);"),
        ("storage_origin_witness", "ALTER TABLE trnm_storage_objects ADD CONSTRAINT storage_origin_witness CHECK (((value_origin = 'legacy-rust-v2-bytes' OR value_origin = 'write-request-bytes') AND value_bytes IS NOT NULL AND version_digest IS NOT NULL AND octet_length(version_digest) = 32 AND source_manifest_digest IS NULL) OR (value_origin = 'nakama-export-unknown-request' AND value_bytes IS NULL AND version_digest IS NULL AND source_manifest_digest IS NOT NULL AND octet_length(source_manifest_digest) = 32 AND source_manifest_digest <> decode(repeat('0',64),'hex')));"),
        ("metadata_v2_history", "ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v2_history CHECK (schema_version < 3 OR (v2_apply_source_commit IS NOT NULL AND length(v2_apply_source_commit) = 40));"),
    )


def validate_action_source(data: bytes, profile: str, revision: int) -> list[str]:
    """Require the exact reviewed sequence and one statement per declaration."""
    declarations = reviewed_actions(profile, revision)
    # BEGIN/COMMIT are historical PostgreSQL v2 wrappers, not v3 actions.
    expected = []
    if profile == "postgresql" and revision == 2:
        expected.append("BEGIN;")
    for action, statement in declarations:
        expected.extend((f"-- trnm:action {action}", statement))
    if profile == "postgresql" and revision == 2:
        expected.append("COMMIT;")
    actual = []
    for line in data.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--") and not line.startswith("-- trnm:"):
            # Comments have no executable or authority-bearing content.
            continue
        actual.append(line)
    require(actual == expected, f"{profile}: revision {revision} action grammar drift")
    return [action for action, _ in declarations]


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
    require(lock.get("generated_from_base") == FROZEN_BASE, "frozen migration base drifted")
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
        action_ids: list[str] = []
        revision_actions: dict[str, int] = {}
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
            if position < 2:
                require(FROZEN_HISTORICAL_BLOBS.get(path_value) == actual_blob,
                        f"{path_value}: frozen historical identity drift")
            if position > 0:
                revision = position + 1
                ids = validate_action_source(data, profile, revision)
                require(not set(ids).intersection(action_ids), f"{profile}: duplicate action identity")
                action_ids.extend(ids)
                revision_actions[str(revision)] = len(ids)
            chain_entries.append((path_value, actual_blob))
        require(listed_paths == actual_sql, f"{profile}: unlisted, missing or unordered SQL migration")
        report[profile] = {
            "file_count": len(listed_paths),
            "ordered_paths": listed_paths,
            "chain_sha256": ordered_chain_digest(chain_entries),
            "digest_algorithm": DIGEST_ALGORITHM,
            "declared_action_count": len(action_ids),
            "revision_action_counts": revision_actions,
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
