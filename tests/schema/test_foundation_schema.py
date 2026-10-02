from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-foundation-schema.py"
SPEC = importlib.util.spec_from_file_location("foundation_schema", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FoundationSchemaTests(unittest.TestCase):
    def copy_tree(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        for relative in (
            "contracts/database",
            "contracts/server",
            "migrations/postgresql",
            "migrations/cockroachdb",
            "docs/development",
        ):
            source = ROOT / relative
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
        self.addCleanup(lambda: shutil.rmtree(temporary))
        shutil.copy2(ROOT / "migrations/MIGRATION_CHAIN.lock.json", temporary / "migrations/MIGRATION_CHAIN.lock.json")
        shutil.copy2(ROOT / "docs/OPERATIONS_AND_RELEASE.md", temporary / "docs/OPERATIONS_AND_RELEASE.md")
        return temporary

    def test_candidate_profiles_pass_static_contract(self) -> None:
        result = module.validate(ROOT)
        self.assertEqual(result["status"], "foundation-schema-static-contract-passed")
        self.assertFalse(result["runtime_execution_verified"])
        self.assertEqual(result["schema_version"], 4)
        self.assertEqual(result["storage_writer_epoch"], 4)
        self.assertTrue(result["pg_source_contract_connected"])
        self.assertEqual([item["table_count"] for item in result["profiles"]], [10, 10])
        for item in result["profiles"]:
            self.assertEqual(len(item["ordered_paths"]), 4)
            self.assertEqual(item["file_count"], 4)
            self.assertEqual(item["timestamp_upgrade_action_count"], 6)
            self.assertNotIn("action_count", item)
            self.assertEqual(item["declared_action_count"], 30 if item["profile"] == "postgresql" else 34)
            self.assertEqual(item["revision_action_counts"], {"2": 6, "3": 16, "4": 8 if item["profile"] == "postgresql" else 12})
            self.assertEqual(item["digest_algorithm"], "ordered-path-git-blob-sha256.v1")
            self.assertTrue(item["historical_timestamps_remain_unknown"])

    def test_missing_unique_entity_revision_is_rejected(self) -> None:
        root = self.copy_tree()
        path = root / "migrations/postgresql/0001_foundation_up.sql"
        path.write_text(path.read_text().replace("    UNIQUE (entity_id, revision),\n", ""), encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_raw_refresh_token_column_is_rejected(self) -> None:
        root = self.copy_tree()
        path = root / "migrations/postgresql/0001_foundation_up.sql"
        value = path.read_text().replace(
            "    token_digest BYTEA NOT NULL UNIQUE CHECK (octet_length(token_digest) = 32),",
            "    refresh_token TEXT NOT NULL,",
        )
        path.write_text(value, encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_cross_profile_binary_type_is_rejected(self) -> None:
        root = self.copy_tree()
        path = root / "migrations/cockroachdb/0001_foundation_up.sql"
        path.write_text(path.read_text().replace("entity_id BYTES", "entity_id BYTEA", 1), encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_positive_claim_is_rejected(self) -> None:
        root = self.copy_tree()
        path = root / "contracts/database/foundation-schema.v1.json"
        value = json.loads(path.read_text())
        value["claims"]["database_durable"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_drop_based_rollback_is_rejected(self) -> None:
        root = self.copy_tree()
        path = root / "migrations/postgresql/0001_foundation_up.sql"
        path.write_text(path.read_text() + "\nDROP TABLE trnm_events;\n", encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_timestamp_defaults_backfill_and_wrong_types_are_rejected(self) -> None:
        root = self.copy_tree()
        for profile in ("postgresql", "cockroachdb"):
            path = root / f"migrations/{profile}/0002_storage_timestamps_up.sql"
            original = path.read_text()
            for replacement in (
                "create_time TIMESTAMPTZ DEFAULT now()",
                "create_time TIMESTAMPTZ NOT NULL",
                "create_time BIGINT",
            ):
                with self.subTest(profile=profile, replacement=replacement):
                    path.write_text(original.replace("create_time TIMESTAMPTZ", replacement), encoding="utf-8")
                    with self.assertRaises(module.SchemaError):
                        module.inspect_upgrade(profile, path)
            path.write_text(original + "\nUPDATE trnm_storage_objects SET create_time = now();\n", encoding="utf-8")
            with self.assertRaises(module.SchemaError):
                module.inspect_upgrade(profile, path)

    def test_upgrade_action_order_and_transaction_boundaries_are_profile_specific(self) -> None:
        root = self.copy_tree()
        pg = root / "migrations/postgresql/0002_storage_timestamps_up.sql"
        original = pg.read_text()
        pg.write_text(original.replace("BEGIN;", ""), encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.inspect_upgrade("postgresql", pg)
        pg.write_text(original.replace("metadata_chain_digest", "metadata_digest_algorithm", 1), encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.inspect_upgrade("postgresql", pg)
        cr = root / "migrations/cockroachdb/0002_storage_timestamps_up.sql"
        cr.write_text("BEGIN;\n" + cr.read_text() + "\nCOMMIT;\n", encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.inspect_upgrade("cockroachdb", cr)

    def test_missing_second_step_and_unlisted_migration_are_rejected(self) -> None:
        root = self.copy_tree()
        (root / "migrations/postgresql/0002_storage_timestamps_up.sql").unlink()
        with self.assertRaises(module.SchemaError):
            module.validate(root)
        root = self.copy_tree()
        (root / "migrations/cockroachdb/0003_unlisted_up.sql").write_text("SELECT 1;\n", encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_v1_only_authority_pointer_and_wrong_digest_algorithm_are_rejected(self) -> None:
        root = self.copy_tree()
        path = root / "contracts/database/foundation-schema.v1.json"
        original = json.loads(path.read_text())
        for key, value in (("migration_lock", "migrations/postgresql/0001_foundation_up.sql"),
                           ("digest_algorithm", "raw-content-historical.v1"),
                           ("schema_version", 1), ("storage_writer_epoch", 1),
                           ("schema_version", 2), ("storage_writer_epoch", 2)):
            candidate = dict(original)
            candidate[key] = value
            path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(module.SchemaError):
                module.validate(root)

    def test_current_identity_must_match_native_jsonb_authority(self) -> None:
        root = self.copy_tree()
        path = root / "docs/development/SCHEMA_AUTHORITY.json"
        original = json.loads(path.read_text())
        for target, key, value in (
            ("authority", "schema_version", 2),
            ("authority", "schema_version", 3.0),
            ("native", "schema_version", 2),
            ("native", "schema_version", 3.0),
            ("native", "storage_writer_epoch", 2),
            ("native", "storage_writer_epoch", 3.0),
        ):
            with self.subTest(target=target, key=key, value=value):
                candidate = json.loads(json.dumps(original))
                selected = candidate["authority"]
                if target == "native":
                    selected = selected["storage_jsonb_upgrade_source_candidate"]
                selected[key] = value
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(module.SchemaError):
                    module.validate(root)

    def test_missing_third_revision_or_v2_only_locked_chain_is_rejected(self) -> None:
        for profile in ("postgresql", "cockroachdb"):
            with self.subTest(profile=profile):
                root = self.copy_tree()
                (root / f"migrations/{profile}/0003_storage_jsonb_up.sql").unlink()
                with self.assertRaises(module.SchemaError):
                    module.validate(root)
        root = self.copy_tree()
        path = root / "migrations/MIGRATION_CHAIN.lock.json"
        lock = json.loads(path.read_text())
        lock["schema_version"] = 2
        for profile in ("postgresql", "cockroachdb"):
            lock["profiles"][profile]["ordered_files"].pop()
            (root / f"migrations/{profile}/0003_storage_jsonb_up.sql").unlink()
        path.write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaises(module.SchemaError):
            module.validate(root)

    def test_relocking_v3_cannot_bypass_shared_closed_native_actions(self) -> None:
        for profile in ("postgresql", "cockroachdb"):
            for original, replacement in (
                ("VARCHAR(32)", "VARCHAR(31)"),
                ("-- trnm:backfill storage_jsonb_v3", "-- trnm:backfill guessed_request_bytes"),
                ("-- trnm:action storage_projection_digest", "-- trnm:action unreviewed_projection_digest"),
            ):
                with self.subTest(profile=profile, replacement=replacement):
                    root = self.copy_tree()
                    relative = f"migrations/{profile}/0003_storage_jsonb_up.sql"
                    path = root / relative
                    source = path.read_text()
                    changed = source.replace(original, replacement, 1)
                    self.assertNotEqual(source, changed)
                    path.write_text(changed, encoding="utf-8")
                    data = path.read_bytes()
                    lock_path = root / "migrations/MIGRATION_CHAIN.lock.json"
                    lock = json.loads(lock_path.read_text())
                    lock["profiles"][profile]["ordered_files"][2]["git_blob_sha1"] = hashlib.sha1(
                        f"blob {len(data)}\0".encode("ascii") + data, usedforsecurity=False,
                    ).hexdigest()
                    lock_path.write_text(json.dumps(lock), encoding="utf-8")
                    with self.assertRaisesRegex(module.SchemaError, "action grammar drift"):
                        module.validate(root)

    def test_public_version_ack_and_witness_contracts_cannot_be_conflated(self) -> None:
        root = self.copy_tree()
        path = root / "contracts/database/foundation-schema.v1.json"
        original = json.loads(path.read_text())
        for section, key, value in (
            ("storage_native_jsonb", "payload_authority", "value_bytes"),
            ("storage_native_jsonb", "stored_public_version", "parsed MD5 hex"),
            ("storage_native_jsonb", "write_ack_version", "MD5 of rendered JSONB"),
            ("storage_native_jsonb", "unknown_raw_witness", "reconstructed request bytes"),
            ("storage_native_jsonb", "catalog_semantic_scope", "all constraints are semantically equivalent"),
            ("storage_native_jsonb", "runtime_execution_credit", True),
            ("storage_native_jsonb", "runtime_execution_credit", 0),
            ("historical_timestamp_upgrade", "action_count", 22),
            ("historical_timestamp_upgrade", "storage_writer_epoch", 3),
        ):
            with self.subTest(section=section, key=key, value=value):
                candidate = json.loads(json.dumps(original))
                candidate[section][key] = value
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(module.SchemaError):
                    module.validate(root)

    def test_pg_source_contract_is_connected_without_promoting_maturity(self) -> None:
        root = self.copy_tree()
        path = root / "contracts/server/pg-vertical-slice-v1.json"
        original = json.loads(path.read_text())
        self.assertIs(original["claims"]["source_candidate"], True)
        for section, key, value in (
            (None, "authoritative_schema_version", 2),
            (None, "storage_writer_epoch", 2),
            (None, "authoritative_migration_lock", "migrations/postgresql/0001_foundation_up.sql"),
            (None, "migration_digest_algorithm", "raw-content-historical.v1"),
            (None, "migration_runner", "diagnostic-v2-only-runner"),
            (None, "schema_verifier", "missing-readonly-verifier"),
            (None, "storage_native_jsonb_contract", "unbound-contract"),
            (None, "required_scenarios", original["required_scenarios"][:-1]),
            ("claims", "source_candidate", False),
            ("claims", "nakama_wire_compatible", True),
            ("claims", "production_ready", None),
        ):
            with self.subTest(section=section, key=key, value=value):
                candidate = json.loads(json.dumps(original))
                selected = candidate if section is None else candidate[section]
                selected[key] = value
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(module.SchemaError):
                    module.validate(root)

    def test_current_candidate_status_does_not_recredit_legacy_foundation_records(self) -> None:
        status = json.loads((ROOT / "docs/status/FOUNDATION_SCHEMA_STATUS.json").read_text())
        candidate = status["current_chain_source_candidate"]
        self.assertEqual(candidate["schema_version"], 4)
        self.assertEqual(candidate["storage_writer_epoch"], 4)
        self.assertEqual(candidate["migrations"], ["0001_foundation_up.sql", "0002_storage_timestamps_up.sql", "0003_storage_jsonb_up.sql", "0004_storage_source_import_up.sql"])
        self.assertIs(candidate["accepted"], False)
        self.assertIs(candidate["migration_compatible"], False)
        self.assertIn("no v2, v3, v4 or full-chain execution credit", candidate["legacy_verified_record_scope"])
        self.assertEqual(status["tested_commit"], "e9b63462fa91383b06706894afed31b378f6b48c")
        self.assertEqual([row["run_id"] for row in status["profiles"]], [33167200252, 33167200252])
        self.assertEqual([row["ddl_blob"] for row in status["profiles"]], ["07f5f4923d884cc63bf53074096b8d1e04215096", "b836b8a2f025ef22525e9e5f089db01ab5f06fe6"])


if __name__ == "__main__":
    unittest.main()
