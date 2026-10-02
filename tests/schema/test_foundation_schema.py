from __future__ import annotations

import importlib.util
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
        self.assertEqual([item["table_count"] for item in result["profiles"]], [10, 10])
        for item in result["profiles"]:
            self.assertEqual(len(item["ordered_paths"]), 2)
            self.assertEqual(item["action_count"], 6)
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
                           ("schema_version", 1), ("storage_writer_epoch", 1)):
            candidate = dict(original)
            candidate[key] = value
            path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(module.SchemaError):
                module.validate(root)


if __name__ == "__main__":
    unittest.main()
