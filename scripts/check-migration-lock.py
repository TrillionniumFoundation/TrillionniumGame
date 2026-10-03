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
SCHEMA_VERSION = 5
DEFAULT_RUNTIME_SCHEMA_VERSION = 4
DIGEST_ALGORITHM = "ordered-path-git-blob-sha256.v1"
FROZEN_BASE = "326e670cb008a990247e31a63c0c4b0e338df62f"
# Adding a revision must never rewrite any reviewed historical revision,
# even if an edited lock also advertises the new blob.
FROZEN_HISTORICAL_BLOBS = {
    "migrations/postgresql/0001_foundation_up.sql": "07f5f4923d884cc63bf53074096b8d1e04215096",
    "migrations/postgresql/0002_storage_timestamps_up.sql": "e36cc2d743eb863af93774a24615f3c5ec1c3443",
    "migrations/cockroachdb/0001_foundation_up.sql": "b836b8a2f025ef22525e9e5f089db01ab5f06fe6",
    "migrations/cockroachdb/0002_storage_timestamps_up.sql": "700cdb460b9211370777928b980b10e37ae21ae9",
    "migrations/postgresql/0003_storage_jsonb_up.sql": "4eb39d906f8ed3444cee7dd7a550ce21dad5e00c",
    "migrations/cockroachdb/0003_storage_jsonb_up.sql": "4eb39d906f8ed3444cee7dd7a550ce21dad5e00c",
    "migrations/postgresql/0004_storage_source_import_up.sql": "12f3f832d6b5509a12573c4d596e5313165307f5",
    "migrations/cockroachdb/0004_storage_source_import_up.sql": "7e996a3e6732ed91508f4354a4d75a786c00e240",
}


def reviewed_storage_import_actions(profile: str) -> tuple[tuple[str, str], ...]:
    """Reviewed v4 ABI only; marker blocks are never a general SQL parser."""
    common = (
        ('storage_import_jobs', "CREATE TABLE trnm_storage_import_jobs (\nsingleton SMALLINT NOT NULL,\nmanifest_digest BYTEA NOT NULL,\ncustody_digest BYTEA NOT NULL,\nsource_inventory_digest BYTEA NOT NULL,\ntarget_schema_guard_digest BYTEA NOT NULL,\nprefix_digest BYTEA NOT NULL,\nsource_profile TEXT NOT NULL,\nsource_snapshot TEXT NOT NULL,\naudit_at_ms BIGINT NOT NULL,\ntotal_rows BIGINT NOT NULL,\ntotal_pages BIGINT NOT NULL,\nnext_page BIGINT NOT NULL,\ncommitted_rows BIGINT NOT NULL,\nstatus SMALLINT NOT NULL,\nCONSTRAINT storage_import_jobs_pk PRIMARY KEY (singleton),\nCONSTRAINT storage_import_jobs_manifest_key UNIQUE (manifest_digest),\nCONSTRAINT storage_import_jobs_singleton CHECK (singleton = 1),\nCONSTRAINT storage_import_jobs_digests CHECK (octet_length(manifest_digest) = 32 AND octet_length(custody_digest) = 32 AND octet_length(source_inventory_digest) = 32 AND octet_length(target_schema_guard_digest) = 32 AND octet_length(prefix_digest) = 32 AND manifest_digest <> decode(repeat('0', 64), 'hex') AND custody_digest <> decode(repeat('0', 64), 'hex') AND source_inventory_digest <> decode(repeat('0', 64), 'hex') AND target_schema_guard_digest <> decode(repeat('0', 64), 'hex') AND prefix_digest <> decode(repeat('0', 64), 'hex')),\nCONSTRAINT storage_import_jobs_source CHECK ((source_profile = 'postgresql' OR source_profile = 'cockroachdb') AND length(source_snapshot) BETWEEN 1 AND 256 AND source_snapshot !~ '[[:cntrl:]]'),\nCONSTRAINT storage_import_jobs_audit CHECK (audit_at_ms >= 0),\nCONSTRAINT storage_import_jobs_counts CHECK (total_rows BETWEEN 0 AND 10000 AND total_pages BETWEEN 0 AND 100 AND next_page BETWEEN 0 AND total_pages AND committed_rows BETWEEN 0 AND total_rows AND ((total_rows = 0 AND total_pages = 0) OR (total_pages > 0 AND total_pages <= total_rows AND total_rows <= 100 * total_pages)) AND committed_rows BETWEEN next_page AND 100 * next_page),\nCONSTRAINT storage_import_jobs_status CHECK (status IN (0, 1) AND (status <> 1 OR (next_page = total_pages AND committed_rows = total_rows)))\n);"),
        ('storage_import_pages', "CREATE TABLE trnm_storage_import_pages (\nmanifest_digest BYTEA NOT NULL,\npage_index BIGINT NOT NULL,\nfirst_ordinal BIGINT NOT NULL,\nrow_count BIGINT NOT NULL,\npage_digest BYTEA NOT NULL,\nprefix_digest BYTEA NOT NULL,\naudit_at_ms BIGINT NOT NULL,\nCONSTRAINT storage_import_pages_pk PRIMARY KEY (manifest_digest, page_index),\nCONSTRAINT storage_import_pages_job_fk FOREIGN KEY (manifest_digest) REFERENCES trnm_storage_import_jobs (manifest_digest) ON UPDATE NO ACTION ON DELETE RESTRICT,\nCONSTRAINT storage_import_pages_digests CHECK (octet_length(manifest_digest) = 32 AND octet_length(page_digest) = 32 AND octet_length(prefix_digest) = 32 AND manifest_digest <> decode(repeat('0', 64), 'hex') AND page_digest <> decode(repeat('0', 64), 'hex') AND prefix_digest <> decode(repeat('0', 64), 'hex')),\nCONSTRAINT storage_import_pages_bounds CHECK (page_index BETWEEN 0 AND 99 AND first_ordinal BETWEEN 0 AND 9999 AND row_count BETWEEN 1 AND 100 AND first_ordinal + row_count <= 10000),\nCONSTRAINT storage_import_pages_audit CHECK (audit_at_ms >= 0)\n);"),
        ('metadata_v3_history', 'ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v3_history CHECK (schema_version < 4 OR (v3_apply_source_commit IS NOT NULL AND length(v3_apply_source_commit) = 40));'),
    )
    if profile == "postgresql":
        return (
            ('metadata_v3_apply_source_commit', 'ALTER TABLE trnm_schema_metadata ADD COLUMN v3_apply_source_commit TEXT;'),
            ('storage_collection_check_v4', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_collection_check, ADD CONSTRAINT trnm_storage_objects_collection_check CHECK (length(collection) BETWEEN 0 AND 128);'),
            ('storage_object_key_check_v4', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_object_key_check, ADD CONSTRAINT trnm_storage_objects_object_key_check CHECK (length(object_key) BETWEEN 0 AND 128);'),
            ('storage_read_permission_check_v4', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_read_permission_check, ADD CONSTRAINT trnm_storage_objects_read_permission_check CHECK (read_permission >= 0);'),
            ('storage_write_permission_check_v4', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT trnm_storage_objects_write_permission_check, ADD CONSTRAINT trnm_storage_objects_write_permission_check CHECK (write_permission >= 0);'),
        ) + common
    require(profile == "cockroachdb", "unknown import action profile")
    return (
        ('metadata_v3_apply_source_commit', 'ALTER TABLE trnm_schema_metadata ADD COLUMN v3_apply_source_commit TEXT;'),
        ('storage_collection_check_v3_remove', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT check_collection;'),
        ('storage_collection_check_v4_add', 'ALTER TABLE trnm_storage_objects ADD CONSTRAINT check_collection CHECK (length(collection) BETWEEN 0 AND 128);'),
        ('storage_object_key_check_v3_remove', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT check_object_key;'),
        ('storage_object_key_check_v4_add', 'ALTER TABLE trnm_storage_objects ADD CONSTRAINT check_object_key CHECK (length(object_key) BETWEEN 0 AND 128);'),
        ('storage_read_permission_check_v3_remove', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT check_read_permission;'),
        ('storage_read_permission_check_v4_add', 'ALTER TABLE trnm_storage_objects ADD CONSTRAINT check_read_permission CHECK (read_permission >= 0);'),
        ('storage_write_permission_check_v3_remove', 'ALTER TABLE trnm_storage_objects DROP CONSTRAINT check_write_permission;'),
        ('storage_write_permission_check_v4_add', 'ALTER TABLE trnm_storage_objects ADD CONSTRAINT check_write_permission CHECK (write_permission >= 0);'),
    ) + common


def reviewed_accounts_actions(profile: str) -> tuple[tuple[str, str], ...]:
    """Exact schema5 source statements; never native catalog observations."""
    require(profile in EXPECTED_PROFILES, "unknown account action profile")
    return (('metadata_v4_apply_source_commit', 'ALTER TABLE trnm_schema_metadata ADD COLUMN v4_apply_source_commit TEXT;'), ('nakama_users', "CREATE TABLE public.users (\nid UUID NOT NULL,\nusername VARCHAR(128) NOT NULL,\ndisplay_name VARCHAR(255),\navatar_url VARCHAR(512),\nlang_tag VARCHAR(18) NOT NULL DEFAULT 'en',\nlocation VARCHAR(255),\ntimezone VARCHAR(255),\nmetadata JSONB NOT NULL DEFAULT '{}',\nwallet JSONB NOT NULL DEFAULT '{}',\nemail VARCHAR(255),\npassword BYTEA,\nfacebook_id VARCHAR(128),\ngoogle_id VARCHAR(128),\ngamecenter_id VARCHAR(128),\nsteam_id VARCHAR(128),\ncustom_id VARCHAR(128),\nedge_count INT NOT NULL DEFAULT 0,\ncreate_time TIMESTAMPTZ NOT NULL DEFAULT now(),\nupdate_time TIMESTAMPTZ NOT NULL DEFAULT now(),\nverify_time TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00 UTC',\ndisable_time TIMESTAMPTZ NOT NULL DEFAULT '1970-01-01 00:00:00 UTC',\nfacebook_instant_game_id VARCHAR(128),\napple_id VARCHAR(128),\nCONSTRAINT users_pkey PRIMARY KEY (id),\nCONSTRAINT users_username_key UNIQUE (username),\nCONSTRAINT users_email_key UNIQUE (email),\nCONSTRAINT users_facebook_id_key UNIQUE (facebook_id),\nCONSTRAINT users_google_id_key UNIQUE (google_id),\nCONSTRAINT users_gamecenter_id_key UNIQUE (gamecenter_id),\nCONSTRAINT users_steam_id_key UNIQUE (steam_id),\nCONSTRAINT users_custom_id_key UNIQUE (custom_id),\nCONSTRAINT users_facebook_instant_game_id_key UNIQUE (facebook_instant_game_id),\nCONSTRAINT users_apple_id_key UNIQUE (apple_id),\nCONSTRAINT users_password_check CHECK (length(password) < 32000),\nCONSTRAINT users_edge_count_check CHECK (edge_count >= 0)\n);"), ('nakama_system_user', "INSERT INTO public.users (id, username) VALUES ('00000000-0000-0000-0000-000000000000', '') ON CONFLICT (id) DO NOTHING;"), ('nakama_user_device', "CREATE TABLE public.user_device (\nid VARCHAR(128) NOT NULL,\nuser_id UUID NOT NULL,\npreferences JSONB NOT NULL DEFAULT '{}',\npush_token_amazon VARCHAR(512) NOT NULL DEFAULT '',\npush_token_android VARCHAR(512) NOT NULL DEFAULT '',\npush_token_huawei VARCHAR(512) NOT NULL DEFAULT '',\npush_token_ios VARCHAR(512) NOT NULL DEFAULT '',\npush_token_web VARCHAR(512) NOT NULL DEFAULT '',\nCONSTRAINT user_device_pkey PRIMARY KEY (id),\nCONSTRAINT user_device_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users (id) ON UPDATE NO ACTION ON DELETE CASCADE,\nCONSTRAINT user_device_user_id_id_key UNIQUE (user_id, id)\n);"), ('metadata_v4_history', 'ALTER TABLE trnm_schema_metadata ADD CONSTRAINT metadata_v4_history CHECK (schema_version < 5 OR (v4_apply_source_commit IS NOT NULL AND length(v4_apply_source_commit) = 40));'))


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
    if revision == 4:
        return reviewed_storage_import_actions(profile)
    if revision == 5:
        return reviewed_accounts_actions(profile)
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
    if (profile == "postgresql" and revision == 2) or revision in (4, 5):
        expected.append("BEGIN;")
    for action, statement in declarations:
        expected.append(f"-- trnm:action {action}")
        expected.extend(statement.splitlines())
    if (profile == "postgresql" and revision == 2) or revision in (4, 5):
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
    require(lock.get("default_runtime_schema_version") == DEFAULT_RUNTIME_SCHEMA_VERSION,
            "default runtime schema profile drift")
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
            if position > 0:
                revision = position + 1
                ids = validate_action_source(data, profile, revision)
                require(not set(ids).intersection(action_ids), f"{profile}: duplicate action identity")
                action_ids.extend(ids)
                revision_actions[str(revision)] = len(ids)
            if position < 4:
                require(FROZEN_HISTORICAL_BLOBS.get(path_value) == actual_blob,
                        f"{path_value}: frozen historical identity drift")
            chain_entries.append((path_value, actual_blob))
        require(listed_paths == actual_sql, f"{profile}: unlisted, missing or unordered SQL migration")
        prefixes = {str(v): {"file_count": v, "ordered_paths": listed_paths[:v],
            "chain_sha256": ordered_chain_digest(chain_entries[:v]), "digest_algorithm": DIGEST_ALGORITHM}
            for v in range(1, SCHEMA_VERSION + 1)}
        report[profile] = {
            "file_count": len(listed_paths),
            "ordered_paths": listed_paths,
            "chain_sha256": ordered_chain_digest(chain_entries),
            "digest_algorithm": DIGEST_ALGORITHM,
            "declared_action_count": len(action_ids),
            "revision_action_counts": revision_actions,
            "revision_prefixes": prefixes,
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
        "default_runtime_schema_version": DEFAULT_RUNTIME_SCHEMA_VERSION,
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
