from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-rust-server-slice.py"
STATUS = ROOT / "docs/status/RUST_SERVER_VERTICAL_SLICE_STATUS.json"
PRODUCT_CLAIMS = {
    "nakama_wire_compatible",
    "database_durable",
    "sg4_complete",
    "compatibility_credit",
    "production_ready",
    "public_online",
    "nakama_replaced",
}


class RustServerSliceContractTests(unittest.TestCase):
    def test_checker_passes_as_a_subprocess(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["compatibility_credit"])
        self.assertTrue(result["claims_all_false"])
        self.assertEqual(
            result["canonical_server"],
            "crates/trnm-persistence-pg/src/bin/trnm-server.rs",
        )

    def test_status_remains_fail_closed(self) -> None:
        status = json.loads(STATUS.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "source-candidate")
        self.assertTrue(status["not_implemented"])
        claims = status["claims"]
        self.assertTrue(claims["source_vertical_slice_exists"])
        self.assertTrue(PRODUCT_CLAIMS.issubset(claims))
        self.assertFalse(any(claims[name] for name in PRODUCT_CLAIMS))

    def test_checker_module_has_no_import_side_effect_failure(self) -> None:
        spec = importlib.util.spec_from_file_location("check_rust_server_slice", CHECKER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.validate()
        self.assertEqual(result["source_tokens"], 16)


if __name__ == "__main__":
    unittest.main()
