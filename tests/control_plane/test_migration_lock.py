from __future__ import annotations

import importlib.util
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
    def test_authoritative_profiles_are_complete_and_distinct(self) -> None:
        result = load_module().validate()
        self.assertEqual(
            set(result["profiles"]),
            {"postgresql", "cockroachdb"},
        )
        self.assertTrue(result["source_identity_verified"])
        self.assertFalse(result["runtime_execution_verified"])
        self.assertFalse(result["compatibility_credit"])
        self.assertNotEqual(
            result["profiles"]["postgresql"]["chain_sha256"],
            result["profiles"]["cockroachdb"]["chain_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
