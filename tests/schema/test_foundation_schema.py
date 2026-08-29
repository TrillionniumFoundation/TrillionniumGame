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
        return temporary

    def test_candidate_profiles_pass_static_contract(self) -> None:
        result = module.validate(ROOT)
        self.assertEqual(result["status"], "foundation-schema-static-contract-passed")
        self.assertFalse(result["runtime_execution_verified"])
        self.assertEqual([item["table_count"] for item in result["profiles"]], [10, 10])

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


if __name__ == "__main__":
    unittest.main()
