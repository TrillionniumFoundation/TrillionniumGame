from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/inventory-go-runtime.py"


class GoRuntimeInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("inventory_go_runtime", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_repository_inventory_is_nonempty_and_fail_closed(self) -> None:
        result = self.module.inventory()
        self.assertGreater(result["source_file_count"], 0)
        self.assertGreater(result["registration_count"], 0)
        self.assertTrue(result["claims"]["inventory_generated"])
        self.assertFalse(result["claims"]["go_sources_migrated"])
        self.assertFalse(result["claims"]["runtime_compatible"])
        self.assertFalse(result["claims"]["production_ready"])

    def test_inventory_is_deterministic(self) -> None:
        self.assertEqual(self.module.inventory(), self.module.inventory())

    def test_cli_writes_machine_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inventory.json"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output), "--require-registrations"],
                cwd=ROOT,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(value["schema"], "trillionnium.go-runtime-migration-inventory.v1")
            self.assertEqual(value["registration_count"], len(value["registrations"]))
            self.assertFalse(value["claims"]["go_sources_migrated"])


if __name__ == "__main__":
    unittest.main()
