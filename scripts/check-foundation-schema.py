#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/database/foundation-schema.v1.json"
PG_CONTRACT = ROOT / "contracts/server/pg-vertical-slice-v1.json"
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+([a-z0-9_]+)", re.IGNORECASE)
CREATE_INDEX = re.compile(r"CREATE\s+INDEX\s+([a-z0-9_]+)", re.IGNORECASE)
DIGEST_ALGORITHM = "ordered-path-git-blob-sha256.v1"
SOURCE_SCHEMA_VERSION = 5
SELECTED_RUNTIME_SCHEMA_VERSION = 4
SELECTED_STORAGE_WRITER_EPOCH = 4
ACCOUNTS_SOURCE_CANDIDATE = {'target': 'NakamaAccountsV5', 'storage_writer_epoch': 4, 'migration': '0005_nakama_accounts_up.sql', 'tables': ['users', 'user_device'], 'full_current_two_table_fields_defaults_constraints': True, 'native_catalog_observations_bound': False, 'native_activation_allowed': False, 'default_runtime_promotion_allowed': False, 'repository_service_routes_implemented': False, 'account_transfer_implemented': False, 'accounts_v5_backups_restores_qualified': False, 'blocked_before_ddl': 'schema5_native_catalog_capture_pending', 'accepted': False, 'boundary': 'Default serve/migrate/import remains strict schema4 prefix. Schema5 source frontier is explicit and cannot borrow epoch4, storage packet or old catalog observations.'}
UPGRADE_COLUMNS = (
    ("metadata_chain_digest", "trnm_schema_metadata", "chain_digest", "string"),
    ("metadata_digest_algorithm", "trnm_schema_metadata", "digest_algorithm", "string"),
    ("metadata_storage_writer_epoch", "trnm_schema_metadata", "storage_writer_epoch", "integer"),
    ("metadata_upgrade_source_commit", "trnm_schema_metadata", "upgrade_source_commit", "string"),
    ("storage_create_time", "trnm_storage_objects", "create_time", "timestamp"),
    ("storage_update_time", "trnm_storage_objects", "update_time", "timestamp"),
)
HISTORICAL_TIMESTAMP_UPGRADE = {
    "schema_version": 2,
    "storage_writer_epoch": 2,
    "migration": "0002_storage_timestamps_up.sql",
    "action_count": 6,
    "historical_timestamps_remain_unknown": True,
}
STORAGE_NATIVE_JSONB = {
    "payload_authority": "value_jsonb",
    "read_projection": "native JSONB::TEXT",
    "stored_public_version": "opaque native VARCHAR(32); no MD5 parsing or normalization",
    "write_ack_version": "MD5 of exact incoming request bytes",
    "projection_integrity": "SHA256 of native JSONB::TEXT UTF-8 bytes",
    "known_request_origins": ["legacy-rust-v2-bytes", "write-request-bytes"],
    "known_raw_witness": "value_bytes/version_digest retain exact known bytes and SHA256; raw MD5, public version and native projection remain bound",
    "unknown_request_origin": "nakama-export-unknown-request",
    "unknown_raw_witness": "SQL NULL value_bytes/version_digest pair with an independent nonzero 32-byte source_manifest_digest",
    "catalog_semantic_scope": "Exact reviewed column typmod, named validated CHECK and primary-key order for this ABI; no general constraint or schema equivalence credit",
    "runtime_execution_credit": False,
}
STORAGE_SOURCE_IMPORT = {'migration': '0004_storage_source_import_up.sql', 'stored_key_domain': '0..128 Unicode characters including empty, dot and control text admitted by the native profile', 'stored_permissions': 'native checked SMALLINT 0..32767; no shared enum authorization predicate', 'acl_predicates': {'batch_read': 'read==2 OR (read==1 AND owner==caller)', 'public_all_list': 'read>=2', 'own_owner_list': 'read>=1', 'foreign_global_list': 'read==2', 'client_write': 'write==1', 'client_delete': 'write>0'}, 'journal_tables': ['trnm_storage_import_jobs', 'trnm_storage_import_pages'], 'history_publishers': ['v2_apply_source_commit', 'v3_apply_source_commit'], 'original_request_bytes': 'unknown source-native export retains NULL raw witness pair; no source text is original request', 'completion': 'single manifest-bound journal; applying prefix blocked by per-transaction and startup readiness admission', 'runtime_execution_credit': False}
PG_FALSE_CLAIMS = {
    "live_postgresql_executed", "restart_duplicate_replay_verified", "cockroachdb_executed",
    "nakama_wire_compatible", "sg4_complete", "production_ready",
}
PG_REQUIRED_SCENARIOS = [
    "fresh migration apply",
    "complete ordered chain with schema v4 and storage writer epoch 4",
    "entity bootstrap",
    "first command commit",
    "one receipt, event, outbox and command-outbox link",
    "process exit and restart",
    "exact duplicate receipt replay after restart",
    "stale expected revision rejection",
    "no duplicate durable rows",
]


class SchemaError(RuntimeError):
    pass


def locked_chain(root: Path) -> dict[str, object]:
    script = Path(__file__).with_name("check-migration-lock.py")
    spec = importlib.util.spec_from_file_location("foundation_migration_lock", script)
    if spec is None or spec.loader is None:
        raise SchemaError("migration-lock checker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.validate(root)
    except (module.ValidationError, OSError, ValueError) as error:
        raise SchemaError(f"authoritative migration lock: {error}") from error


def normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).lower()


def inspect_profile(
    profile: str,
    path: Path,
    contract: dict[str, object],
    root: Path,
) -> dict[str, object]:
    sql = path.read_text(encoding="utf-8")
    normalized = normalize(sql)
    tables = CREATE_TABLE.findall(sql)
    indexes = CREATE_INDEX.findall(sql)
    required = contract["required_tables"]
    if set(tables) != set(required):
        missing = sorted(set(required) - set(tables))
        extra = sorted(set(tables) - set(required))
        raise SchemaError(
            f"{profile}: table mismatch missing={missing} extra={extra}"
        )
    if len(tables) != len(set(tables)):
        raise SchemaError(f"{profile}: duplicate CREATE TABLE")

    expected_binary = contract["profiles"][profile]["binary_type"].lower()
    if expected_binary not in normalized:
        raise SchemaError(f"{profile}: expected binary type {expected_binary}")
    wrong_binary = "bytes" if expected_binary == "bytea" else "bytea"
    if re.search(rf"\b{wrong_binary}\b", normalized):
        raise SchemaError(
            f"{profile}: contains other profile binary type {wrong_binary}"
        )

    for pattern in (
        r"\bserial\b",
        r"\bbigserial\b",
        r"\buuid\b\s+default",
        r"default\s+now\s*\(",
        r"default\s+clock_timestamp\s*\(",
        r"\btoken\s+(text|string|bytea|bytes)\b",
        r"\baccess_token\b",
        r"\brefresh_token\b(?!s)",
        r"drop\s+table",
        r"cascade",
    ):
        if re.search(pattern, normalized):
            raise SchemaError(f"{profile}: forbidden SQL pattern {pattern}")

    for fragment in (
        "primary key (entity_id, command_id)",
        "unique (entity_id, revision)",
        "primary key (entity_id, sequence)",
        "intent_id",
        "token_digest",
        "unique check (octet_length(token_digest) = 32)",
        "primary key (collection, object_key, user_id)",
        "state between 0 and 3",
        "lease_generation",
        "authority_generation",
        "on delete restrict",
        "event_count <= 64",
        "attempt <= 32",
    ):
        if fragment not in normalized:
            raise SchemaError(f"{profile}: missing contract fragment {fragment}")

    if normalized.count("begin;") != 1 or normalized.count("commit;") != 1:
        raise SchemaError(f"{profile}: migration must use one explicit transaction")
    if "trnm_outbox_ready_idx" not in indexes:
        raise SchemaError(f"{profile}: missing outbox readiness index")

    return {
        "profile": profile,
        "path": str(path.relative_to(root)),
        "table_count": len(tables),
        "index_count": len(indexes),
        "tables": tables,
    }


def inspect_upgrade(profile: str, path: Path) -> dict[str, object]:
    sql = path.read_text(encoding="utf-8")
    markers = re.findall(r"^-- trnm:action ([a-z_]+)\s*$", sql, flags=re.MULTILINE)
    expected_markers = [row[0] for row in UPGRADE_COLUMNS]
    if markers != expected_markers:
        raise SchemaError(f"{profile}: upgrade action inventory/order differs")
    clean = re.sub(r"--[^\n]*", "", sql)
    statements = [normalize(part) for part in clean.split(";") if part.strip()]
    if profile == "postgresql":
        if not statements or statements[0] != "begin" or statements[-1] != "commit":
            raise SchemaError("postgresql: timestamp upgrade must own one transaction")
        statements = statements[1:-1]
    kinds = {
        "string": "text" if profile == "postgresql" else "string",
        "integer": "bigint" if profile == "postgresql" else "int8",
        "timestamp": "timestamptz",
    }
    expected = [f"alter table {table} add column {name} {kinds[kind]}"
                for _, table, name, kind in UPGRADE_COLUMNS]
    if statements != expected:
        raise SchemaError(f"{profile}: timestamp upgrade must append exactly six nullable columns without defaults/backfill")
    return {"action_count": len(statements), "historical_timestamps_remain_unknown": True}


def validate_current_contracts(
    root: Path, contract: dict[str, object], chain: dict[str, object],
) -> None:
    # Shared lock validation owns every v3 action and its immutable source
    # identity. The six-action parser above is only the historical v2 layer.
    authority = json.loads((root / "docs/development/SCHEMA_AUTHORITY.json").read_text())["authority"]
    current = authority["storage_source_import_upgrade_source_candidate"]
    historical_native = authority["storage_jsonb_upgrade_source_candidate"]
    if type(historical_native["schema_version"]) is not int or historical_native["schema_version"] != 3 or type(historical_native["storage_writer_epoch"]) is not int or historical_native["storage_writer_epoch"] != 3:
        raise SchemaError("historical v3 native JSONB authority must retain its exact revision")
    source_version = chain["schema_version"]
    version = chain["default_runtime_schema_version"]
    epoch = current["storage_writer_epoch"]
    if type(source_version) is not int or source_version != SOURCE_SCHEMA_VERSION:
        raise SchemaError("complete source frontier must remain exact schema5")
    if type(version) is not int or version != SELECTED_RUNTIME_SCHEMA_VERSION or type(authority["schema_version"]) is not int or authority["schema_version"] != version or type(current["schema_version"]) is not int or current["schema_version"] != version:
        raise SchemaError("selected runtime authority must remain exact schema4")
    if type(contract.get("default_runtime_schema_version")) is not int or contract["default_runtime_schema_version"] != version:
        raise SchemaError("foundation selected runtime must not follow the source frontier")
    for candidate in (authority.get("accounts_v5_source_candidate"), contract.get("accounts_v5_source_candidate")):
        if json.dumps(candidate, sort_keys=True) != json.dumps(ACCOUNTS_SOURCE_CANDIDATE, sort_keys=True):
            raise SchemaError("Accounts5 source frontier or closed activation gate differs")
    if type(epoch) is not int or epoch != SELECTED_STORAGE_WRITER_EPOCH:
        raise SchemaError("current storage writer epoch differs from reviewed source-import revision")
    if type(contract.get("schema_version")) is not int or contract["schema_version"] != version:
        raise SchemaError("foundation current schema version differs from the complete shared chain")
    if type(contract.get("storage_writer_epoch")) is not int or contract["storage_writer_epoch"] != epoch:
        raise SchemaError("foundation current writer epoch differs from schema authority")
    if json.dumps(contract.get("historical_timestamp_upgrade"), sort_keys=True) != json.dumps(HISTORICAL_TIMESTAMP_UPGRADE, sort_keys=True):
        raise SchemaError("historical v2 timestamp contract differs from its six-action scope")
    if json.dumps(contract.get("storage_native_jsonb"), sort_keys=True) != json.dumps(STORAGE_NATIVE_JSONB, sort_keys=True):
        raise SchemaError("native JSONB/public-version/witness reviewed ABI contract differs")

    if json.dumps(contract.get("storage_source_import"), sort_keys=True) != json.dumps(STORAGE_SOURCE_IMPORT, sort_keys=True):
        raise SchemaError("storage source-import/key/raw-ACL/journal reviewed ABI contract differs")
    if type(contract.get("current_authoritative_table_count")) is not int or contract["current_authoritative_table_count"] != 12:
        raise SchemaError("current authoritative table count must include both import journal tables")

    pg = json.loads((root / PG_CONTRACT.relative_to(ROOT)).read_text())
    if pg.get("schema") != "trillionnium.pg-server-vertical-slice.v1" or pg.get("status") != "source-candidate":
        raise SchemaError("PG source contract envelope/status differs")
    if type(pg.get("authoritative_schema_version")) is not int or pg["authoritative_schema_version"] != version:
        raise SchemaError("PG current schema version differs from the complete shared chain")
    if type(pg.get("storage_writer_epoch")) is not int or pg["storage_writer_epoch"] != epoch:
        raise SchemaError("PG current writer epoch differs from schema authority")
    if pg.get("authoritative_migration_lock") != contract["migration_lock"] or pg.get("migration_digest_algorithm") != DIGEST_ALGORITHM:
        raise SchemaError("PG source contract must consume the complete shared migration identity")
    if pg.get("migration_runner") != "trnm-schema migrate --candidate-plaintext" or pg.get("schema_verifier") != "trnm-schema verify --candidate-plaintext":
        raise SchemaError("PG source contract runner/verifier differs from the shared engine")
    if pg.get("storage_native_jsonb_contract") != "contracts/database/foundation-schema.v1.json#storage_native_jsonb":
        raise SchemaError("PG source contract must consume the reviewed native JSONB ABI")
    if pg.get("storage_source_import_contract") != "contracts/database/foundation-schema.v1.json#storage_source_import":
        raise SchemaError("PG source contract must consume reviewed source-import ABI")
    if pg.get("required_scenarios") != PG_REQUIRED_SCENARIOS:
        raise SchemaError("PG source contract omits the complete current schema scenario")
    claims = pg.get("claims")
    if not isinstance(claims, dict) or set(claims) != PG_FALSE_CLAIMS | {"source_candidate"} or claims["source_candidate"] is not True:
        raise SchemaError("PG source contract candidate identity differs")
    if any(claims[name] is not False for name in PG_FALSE_CLAIMS):
        raise SchemaError("PG source contract overclaims maturity")


def validate(root: Path = ROOT) -> dict[str, object]:
    contract = json.loads(
        (root / CONTRACT.relative_to(ROOT)).read_text(encoding="utf-8")
    )
    if contract.get("schema") != "trillionnium.foundation-schema-contract.v1":
        raise SchemaError("unexpected contract schema")
    if contract.get("migration_lock") != "migrations/MIGRATION_CHAIN.lock.json":
        raise SchemaError("foundation contract must consume the authoritative migration lock")
    if contract.get("digest_algorithm") != DIGEST_ALGORITHM:
        raise SchemaError("foundation migration digest algorithm differs from Rust runner")
    claims = contract.get("claims")
    if not isinstance(claims, dict) or not claims or any(value is not False for value in claims.values()):
        raise SchemaError("foundation schema contract overclaims maturity")
    if contract.get("security", {}).get("raw_session_token_storage_allowed") is not False:
        raise SchemaError("raw session token storage must be false")
    if contract.get("security", {}).get("raw_refresh_token_storage_allowed") is not False:
        raise SchemaError("raw refresh token storage must be false")
    if (
        contract.get("rollback", {}).get("automatic_destructive_down_migration")
        is not False
    ):
        raise SchemaError("automatic destructive rollback must be false")

    chain = locked_chain(root)
    validate_current_contracts(root, contract, chain)
    profiles = []
    for profile in ("postgresql", "cockroachdb"):
        row = chain["profiles"][profile]
        relative = Path(contract["profiles"][profile]["foundation_path"])
        if relative.as_posix() != row["ordered_paths"][0]:
            raise SchemaError(f"{profile}: immutable foundation fixture differs from chain prefix")
        result = inspect_profile(profile, root / relative, contract, root)
        timestamps = inspect_upgrade(profile, root / row["ordered_paths"][1])
        result.update({"timestamp_upgrade_action_count": timestamps["action_count"],
                       "historical_timestamps_remain_unknown": timestamps["historical_timestamps_remain_unknown"],
                       "ordered_paths": row["ordered_paths"], "file_count": row["file_count"],
                       "chain_sha256": row["chain_sha256"], "digest_algorithm": row["digest_algorithm"],
                       "declared_action_count": row["declared_action_count"],
                       "revision_action_counts": row["revision_action_counts"],
                       "selected_runtime_prefix": row["revision_prefixes"][str(SELECTED_RUNTIME_SCHEMA_VERSION)]})
        profiles.append(result)
    if profiles[0]["tables"] != profiles[1]["tables"]:
        raise SchemaError("logical table order differs between profiles")

    operations = (root / "docs/OPERATIONS_AND_RELEASE.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "`migrations/postgresql/` and `migrations/cockroachdb/` are the only production DDL chains",
        "Rollback must account for sessions, parties, tickets, matches, schedulers, IAP transactions, outbox effects and Rust-only schema state.",
        "Crossing an irreversible barrier requires explicit approval and evidence.",
    ):
        if marker not in operations:
            raise SchemaError(f"current operations documentation missing: {marker}")

    return {
        "status": "foundation-schema-static-contract-passed",
        "schema_version": contract["schema_version"],
        "source_schema_version": chain["schema_version"],
        "default_runtime_schema_version": chain["default_runtime_schema_version"],
        "storage_writer_epoch": contract["storage_writer_epoch"],
        "pg_source_contract_connected": True,
        "current_authoritative_table_count": 12,
        "foundation_inspection_scope": "immutable0001 ten-table foundation; complete source5 chain retained separately from selected StorageV4 prefix12 tables/epoch4",
        "profiles": profiles,
        "rollback_authority": "docs/OPERATIONS_AND_RELEASE.md",
        "runtime_execution_verified": False,
        "database_durable": False,
        "migration_compatible": False,
        "production_ready": False,
    }


def main() -> None:
    print(json.dumps(validate(), sort_keys=True))


if __name__ == "__main__":
    main()
