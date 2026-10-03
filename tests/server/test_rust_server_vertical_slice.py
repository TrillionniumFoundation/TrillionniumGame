from __future__ import annotations

import json
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RustServerVerticalSliceTest(unittest.TestCase):
    def test_fail_closed_source_contract(self) -> None:
        spec = importlib.util.spec_from_file_location('rust_server_vertical_slice', ROOT / 'scripts/check-rust-server-vertical-slice.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(module.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "passed-source-contract")
        self.assertFalse(result["compatibility_credit"])
        self.assertFalse(result["database_durable"])
        self.assertFalse(result["sg4_complete"])


if __name__ == "__main__":
    unittest.main()
