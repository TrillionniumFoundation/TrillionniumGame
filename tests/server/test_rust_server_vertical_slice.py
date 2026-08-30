from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RustServerVerticalSliceTest(unittest.TestCase):
    def test_fail_closed_source_contract(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check-rust-server-vertical-slice.py"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "passed-source-contract")
        self.assertFalse(result["compatibility_credit"])
        self.assertFalse(result["database_durable"])
        self.assertFalse(result["sg4_complete"])


if __name__ == "__main__":
    unittest.main()
