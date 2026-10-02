from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-migration-lock.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_migration_lock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationLockTests(unittest.TestCase):
    def copy_migrations(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / "migrations", root / "migrations")
        return root

    def test_authoritative_profiles_are_complete_and_distinct(self) -> None:
        result = load_module().validate()
        self.assertEqual(
            set(result["profiles"]),
            {"postgresql", "cockroachdb"},
        )
        self.assertTrue(result["source_identity_verified"])
        self.assertFalse(result["runtime_execution_verified"])
        self.assertFalse(result["compatibility_credit"])
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["digest_algorithm"], "ordered-path-git-blob-sha256.v1")
        for row in result["profiles"].values():
            self.assertEqual(row["file_count"], 2)
            self.assertEqual(row["digest_algorithm"], result["digest_algorithm"])
        self.assertNotEqual(
            result["profiles"]["postgresql"]["chain_sha256"],
            result["profiles"]["cockroachdb"]["chain_sha256"],
        )

    def test_digest_uses_exact_zero_based_binary_framing(self) -> None:
        module = load_module()
        expected_bytes = bytes(8) + b"a.sql\0" + bytes(20) + b"\0\0\0\0\0\0\0\1b.sql\0" + bytes([0xff]) * 20
        self.assertEqual(module.ordered_chain_digest([("a.sql", "00" * 20), ("b.sql", "ff" * 20)]),
                         hashlib.sha256(expected_bytes).hexdigest())

    def test_old_version_short_chain_reordered_and_blob_drift_are_rejected(self) -> None:
        module = load_module()
        for mutation in ("old_version", "short_chain", "reordered", "blob_drift", "path_escape"):
            root = self.copy_migrations()
            path = root / "migrations/MIGRATION_CHAIN.lock.json"
            value = json.loads(path.read_text())
            entries = value["profiles"]["postgresql"]["ordered_files"]
            if mutation == "old_version":
                value["schema_version"] = 1
            elif mutation == "short_chain":
                entries.pop()
            elif mutation == "reordered":
                entries.reverse()
            elif mutation == "blob_drift":
                entries[1]["git_blob_sha1"] = "00" * 20
            else:
                entries[1]["path"] = "migrations/postgresql/../cockroachdb/0002_storage_timestamps_up.sql"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(module.ValidationError):
                module.validate(root)


if __name__ == "__main__":
    unittest.main()
