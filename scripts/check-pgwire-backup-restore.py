#!/usr/bin/env python3
from __future__ import annotations

import importlib.util

import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
import re
from pathlib import Path
EXPECTED_TABLES = {
    "trnm_schema_metadata",
    "trnm_entity_heads",
    "trnm_command_receipts",
    "trnm_events",
    "trnm_outbox",
    "trnm_command_outbox",
    "trnm_authority_leases",
    "trnm_session_families",
    "trnm_refresh_tokens",
    "trnm_storage_objects",
    "trnm_storage_import_jobs",
    "trnm_storage_import_pages",
}
EMPTY_ALLOWED_TABLES = {"trnm_storage_import_jobs", "trnm_storage_import_pages"}



SCHEMA_SPEC = importlib.util.spec_from_file_location(
    "schema_consumer_source_contract", Path(__file__).with_name("check-schema-authority.py")
)
if SCHEMA_SPEC is None or SCHEMA_SPEC.loader is None:
    raise RuntimeError("schema consumer checker unavailable")
SCHEMA = importlib.util.module_from_spec(SCHEMA_SPEC)
SCHEMA_SPEC.loader.exec_module(SCHEMA)

V3_FIELDS = ("value_jsonb_text", "public_version", "value_projection_digest", "value_origin",
             "source_manifest_digest", "request_native_text")
V3_PROBES = {
    "projection-width": ("23514", "storage_projection_digest"),
    "known-with-manifest": ("23514", "storage_origin_witness"),
    "unknown-with-request": ("23514", "storage_origin_witness"),
    "unknown-zero-manifest": ("23514", "storage_origin_witness"),
    "missing-native": ("23502", "none"),
    "missing-public-version": ("23502", "none"),
    "missing-projection": ("23502", "none"),
    "missing-origin": ("23502", "none"),
    "public-version-width": ("22001", "none"),
}


def validate_v3_seed_source(script: str) -> None:
    """Require independent request/native fingerprints, including unknown history."""
    for marker in ("value_jsonb", "public_version", "value_projection_digest", "value_origin",
                   "source_manifest_digest", "md5(request_text)", "native_value::TEXT",
                   "write-request-bytes", "nakama-export-unknown-request", "unknown-empty",
                   "unknown-unicode", "synthetic storage fixture manifest"):
        if marker not in script:
            raise SystemExit(f"storage v3 seed omits {marker}")
    if "decode('010203','hex')" in script:
        raise SystemExit("storage seed retains an invalid raw-only value")
    if "value_bytes, version_digest, read_permission, write_permission, updated_at_ms) VALUES" in script:
        raise SystemExit("storage seed omits mandatory v3 projection columns")


def validate_v3_snapshot_source(data: str) -> None:
    for field in (*V3_FIELDS, "v2_apply_source_commit", "v3_apply_source_commit", "create_time_text", "update_time_text"):
        if re.search(r"'" + field + r"'\s*,", data) is None:
            raise SystemExit(f"storage v3 snapshot omits {field}")
    if re.search(r"'value_jsonb_text'\s*,\s*value_jsonb::TEXT", data) is None:
        raise SystemExit("storage v3 snapshot must retain exact native JSONB text")
    for table in EMPTY_ALLOWED_TABLES:
        if "FROM " + table not in data:
            raise SystemExit("storage snapshot omits import journal table: " + table)
    if "AS snapshot_row" not in data:
        raise SystemExit("storage v3 snapshot lacks an unambiguous result column")


def validate_v3_semantic_harness(harness: str) -> None:
    validate_v3_seed_source(harness)
    observed = re.findall(r'^storage_v3_constraint ([a-z-]+) ([0-9A-Z]{5}) "([a-z_]+)"',
                          harness, re.MULTILINE)
    if len(observed) != len(V3_PROBES) or {label: (state, name) for label, state, name in observed} != V3_PROBES:
        raise SystemExit("storage v3 structural rejection set differs")
    for marker in ("apply-authoritative-schema.sh\" verify", "restored-schema-identity.json",
                   "--mode verify", "report['schema_version']==4", "report['storage_writer_epoch']==4",
                   "v2_apply_source_commit", "v3_apply_source_commit", "authoritative_migration_file_count", "len(ordered)==4",
                   '"storage_v3_constraint_probe_count": 9', "validate_storage_snapshot_bytes"):
        if marker not in harness:
            raise SystemExit(f"storage v3 recovery assertion missing: {marker}")


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate snapshot JSON field")
        result[key] = value
    return result


def validate_storage_snapshot_bytes(data: bytes, profile: str, identity: dict,
                                    collection: str) -> dict:
    """Inspect executed native SQL output; never reconstruct a native renderer."""
    if profile not in ("postgresql", "cockroachdb") or not 0 < len(data) <= 32 * 1024 * 1024:
        raise ValueError("invalid or unbounded storage snapshot")
    text = data.decode("utf-8", errors="strict")
    if profile == "postgresql":
        lines = text.splitlines()
    else:
        previous_limit = csv.field_size_limit(32 * 1024 * 1024)
        try:
            lines = [row[0] if len(row) == 1 else ""
                     for row in csv.reader(io.StringIO(text), delimiter="\t", strict=True)]
        finally:
            csv.field_size_limit(previous_limit)
    if profile == "cockroachdb":
        if not lines or lines.pop(0) != "snapshot_row":
            raise ValueError("native snapshot result header differs")
    rows = {table: [] for table in EXPECTED_TABLES}
    for line in lines:
        table, separator, raw = line.partition("|")
        if not separator or table not in rows:
            raise ValueError("unexpected native snapshot row")
        value = json.loads(raw, object_pairs_hook=_object_without_duplicates, parse_float=Decimal,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite snapshot")))
        if not isinstance(value, dict):
            raise ValueError("native snapshot row is not an object")
        rows[table].append(value)
    if any(not values for table, values in rows.items() if table not in EMPTY_ALLOWED_TABLES) or len(rows["trnm_schema_metadata"]) != 1:
        raise ValueError("native snapshot omits foundation data")
    metadata = rows["trnm_schema_metadata"][0]
    for field in ("profile", "schema_version", "storage_writer_epoch", "chain_digest", "digest_algorithm",
                  "source_commit", "upgrade_source_commit", "v2_apply_source_commit", "v3_apply_source_commit"):
        if field not in metadata or metadata[field] != identity[field]:
            raise ValueError("native snapshot schema provenance differs")
    if metadata["schema_version"] != 4 or metadata["storage_writer_epoch"] != 4:
        raise ValueError("native snapshot storage ABI differs")
    fixtures = {}
    for row in rows["trnm_storage_objects"]:
        if not all(field in row for field in (*V3_FIELDS, "value_bytes", "version_digest",
                                              "create_epoch", "update_epoch", "create_time_text", "update_time_text")):
            raise ValueError("native snapshot storage columns incomplete")
        native = row["value_jsonb_text"]
        token = row["public_version"]
        if not isinstance(native, str) or not 0 < len(native.encode()) <= 16 * 1024 * 1024:
            raise ValueError("native projection is missing or unbounded")
        if not isinstance(token, str) or len(token) > 32:
            raise ValueError("public storage token is invalid")
        projection = hashlib.sha256(native.encode()).hexdigest()
        if row["value_projection_digest"] != projection:
            raise ValueError("native projection fingerprint differs")
        origin = row["value_origin"]
        if origin in ("legacy-rust-v2-bytes", "write-request-bytes"):
            request_hex, digest = row["value_bytes"], row["version_digest"]
            if not isinstance(request_hex, str) or re.fullmatch(r"(?:[0-9a-f]{2}){1,1048576}", request_hex) is None:
                raise ValueError("known request witness is invalid")
            request = bytes.fromhex(request_hex)
            if hashlib.sha256(request).hexdigest() != digest or hashlib.md5(request).hexdigest() != token:
                raise ValueError("known request fingerprint differs")
            if row["source_manifest_digest"] is not None or row["request_native_text"] != native:
                raise ValueError("known native request binding differs")
        elif origin == "nakama-export-unknown-request":
            manifest = row["source_manifest_digest"]
            if any(row[field] is not None for field in ("value_bytes", "version_digest", "request_native_text")):
                raise ValueError("unknown history invents a request witness")
            if not isinstance(manifest, str) or re.fullmatch(r"[0-9a-f]{64}", manifest) is None or manifest == "0" * 64:
                raise ValueError("unknown history lacks a manifest witness")
        else:
            raise ValueError("storage origin differs")
        if row.get("collection") == collection:
            key = row.get("object_key")
            if key in fixtures:
                raise ValueError("duplicate storage fixture")
            fixtures[key] = row
    if set(fixtures) != {"fixture", "known-time", "unknown-empty", "unknown-unicode"}:
        raise ValueError("storage v3 restore fixture set differs")
    for key in ("fixture", "known-time"):
        row = fixtures[key]
        if row["value_origin"] != "write-request-bytes" or row["public_version"] == hashlib.md5(row["value_jsonb_text"].encode()).hexdigest():
            raise ValueError("restore fixture does not distinguish request and native hashes")
    for key in ("fixture", "unknown-empty", "unknown-unicode"):
        if any(fixtures[key][field] is not None for field in
               ("create_epoch", "update_epoch", "create_time_text", "update_time_text")):
            raise ValueError("unknown history timestamps were invented")
    known = fixtures["known-time"]
    # PostgreSQL extracts decimal epochs; CockroachDB extracts floating epochs.
    # Preserve both executed lexemes in the snapshot and inspect exact native
    # TIMESTAMPTZ text below, without treating a float epoch as microsecond proof.
    if profile == "postgresql" and (known["create_epoch"] != Decimal("-0.000001") or known["update_epoch"] != Decimal("1709164800.123456")):
        raise ValueError("known timestamp microseconds differ")
    if any(not isinstance(known[field], (int, Decimal)) for field in ("create_epoch", "update_epoch")):
        raise ValueError("known timestamp epoch is missing")
    for field, prefix in (("create_time_text", "1969-12-31 23:59:59.999999"),
                          ("update_time_text", "2024-02-29 00:00:00.123456")):
        expected = re.escape(prefix).replace(r"\ ", "[ T]") + r"(?:\+00(?::?00)?|Z)"
        if not isinstance(known[field], str) or re.fullmatch(expected, known[field]) is None:
            raise ValueError("native timestamp precision differs")
    if fixtures["unknown-empty"]["public_version"] != "" or fixtures["unknown-empty"]["value_jsonb_text"] != "null":
        raise ValueError("empty-token JSON-null history differs")
    if fixtures["unknown-unicode"]["public_version"] != "版本A*" or not fixtures["unknown-unicode"]["value_jsonb_text"].startswith("["):
        raise ValueError("opaque-token array history differs")
    return {"schema": "trillionnium.storage-v3-restore-snapshot.v1", "profile": profile,
            "storage_fixture_count": 4, "known_request_witness_count": 2,
            "unknown_request_witness_count": 2, "null_timestamp_fixture_count": 3,
            "known_microsecond_fixture_count": 1, "native_projection_verified": True,
            "independent_public_version_verified": True, "compatibility_credit": False,
            "accepted_evidence": False, "production_ready": False}

def validate_text(script: str, image_config: dict) -> None:
    profiles = image_config.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"postgresql", "cockroachdb"}:
        raise SystemExit("current database image profiles are invalid")
    images = set()
    for profile in ("postgresql", "cockroachdb"):
        row = profiles[profile]
        image = row.get("image") if isinstance(row, dict) else None
        if not isinstance(image, str) or re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image) is None:
            raise SystemExit(f"current {profile} image lacks an immutable OCI digest")
        images.add(image)
    if "config/database-test-images.json" not in script or not re.search(r"\[['\"]image['\"]\]", script):
        raise SystemExit("backup profiles must read their image identity from the current configuration")
    for image in re.findall(r"(?:postgres|cockroachdb/cockroach)(?::[^\s'\"@]+)?@sha256:[0-9a-f]{64}", script):
        if image not in images:
            raise SystemExit(f"backup image differs from current configuration: {image}")
    for forbidden in (":latest", "production_pitr\":true", "multi_node_restore\":true"):
        if forbidden in script:
            raise SystemExit(f"forbidden overclaim or floating input: {forbidden}")
    for required in (
        "pg_dump -Fc",
        "pg_restore --no-owner --no-privileges",
        "BACKUP DATABASE trnm INTO 'nodelocal://1/trnm-backup'",
        "RESTORE DATABASE trnm FROM LATEST",
        'cmp "$evidence/source.csv" "$evidence/restored.csv"',
        'test -s "$evidence/backup.dump"',
        'test -s "$evidence/backup-manifest.csv"',
    ):
        if required not in script:
            raise SystemExit(f"required backup/restore assertion missing: {required}")
    missing_tables = sorted(table for table in EXPECTED_TABLES if table not in script)
    if missing_tables:
        raise SystemExit(f"semantic snapshot omits tables: {missing_tables}")
    if script.count("semantic_snapshot_equal\":true") != 1:
        raise SystemExit("semantic equality claim must be emitted exactly once after cmp")
    try:
        SCHEMA.validate_schema_consumer(script, None)
        SCHEMA.validate_known_timestamp_fixtures(script)
    except SCHEMA.ValidationError as error:
        raise SystemExit(str(error)) from error
    if script.count("apply-authoritative-schema.sh") < 2:
        raise SystemExit("both backup profiles must consume the complete Rust schema chain")
    if script.count("apply-authoritative-schema.sh verify") != 2 or script.count("--mode verify") != 2:
        raise SystemExit("both restored profiles must verify the retained complete schema identity")
    validate_v3_seed_source(script)
    for marker in ("validate_storage_snapshot_bytes", "source-storage-v3.txt", "restored-storage-v3.txt",
                   "storage-v3-snapshot-check.json", "len(ordered) == 4", "fresh['schema_version'] == 4",
                   "fresh['storage_writer_epoch'] == 4"):
        if marker not in script:
            raise SystemExit(f"storage v3 backup assertion missing: {marker}")


def check(root: Path) -> None:
    script = (root / "scripts/ci-pgwire-backup-restore.sh").read_text(encoding="utf-8")
    config = json.loads((root / "config/database-test-images.json").read_text(encoding="utf-8"))
    validate_text(script, config)
    print(
        "backup/restore source contract passed: "
        f"tables={len(EXPECTED_TABLES)} profiles=2 production_pitr=false multi_node=false"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    check(args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
