from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-rust-server-slice.py"
SOURCE_CHECKER = ROOT / "scripts/check-rust-server-source-candidate.py"
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
            [sys.executable, str(CHECKER)], cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["compatibility_credit"])
        self.assertTrue(result["claims_all_false"])
        self.assertTrue(result["authority_transferred_source_candidate"])
        self.assertEqual(
            result["canonical_server"],
            "crates/trnm-server/src/main.rs",
        )
        self.assertEqual(result["diagnostic_server"], "crates/trnm-persistence-pg/src/bin/trnm-server.rs")
        self.assertGreaterEqual(result["source_tokens"], 20)

    def test_standalone_source_checker_passes_as_a_subprocess(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SOURCE_CHECKER)], cwd=ROOT, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema"], "trillionnium.server-source-check.v3")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["binary"], "trnm-server")
        self.assertEqual(result["runtime_module_count"], 32)
        self.assertGreaterEqual(result["source_marker_count"], 20)
        self.assertFalse(result["claims"]["compiled"])
        self.assertFalse(result["claims"]["live_process_executed"])
        self.assertFalse(result["claims"]["live_database_bound"])
        self.assertFalse(result["claims"]["wire_compatible"])
        self.assertFalse(result["claims"]["production_ready"])

    def test_standalone_runtime_inventory_requires_both_native_projection_modules(self) -> None:
        spec = importlib.util.spec_from_file_location("server_source_inventory", SOURCE_CHECKER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        actual = {path.name for path in (ROOT / "crates/trnm-server/src/runtime").glob("*.rs") if path.is_file()}
        module.validate_runtime_file_inventory(actual)
        for omitted in ("storage_api_projection_tests.rs", "storage_api_v3_live.rs",
                        "legacy_auth.rs", "legacy_auth_tests.rs", "legacy_device_predicates.rs",
                        "legacy_repository.rs", "legacy_repository_tests.rs", "legacy_uuid.rs"):
            with self.subTest(omitted=omitted), self.assertRaisesRegex(module.ValidationError, "file set drift"):
                module.validate_runtime_file_inventory(actual - {omitted})
        with self.assertRaisesRegex(module.ValidationError, "file set drift"):
            module.validate_runtime_file_inventory(actual | {"unregistered_runtime.rs"})
        with self.assertRaisesRegex(module.ValidationError, "file set drift"):
            module.validate_runtime_file_inventory((actual - {"storage_api_v3_live.rs"}) | {"unregistered_runtime.rs"})

    def test_status_remains_fail_closed(self) -> None:
        status = json.loads(STATUS.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "source-candidate")
        self.assertTrue(status["not_implemented"])
        claims = status["claims"]
        self.assertTrue(claims["source_vertical_slice_exists"])
        self.assertTrue(claims["composition_source_exists"])
        self.assertTrue(PRODUCT_CLAIMS.issubset(claims))
        self.assertFalse(any(claims[name] for name in PRODUCT_CLAIMS))

    def test_checker_module_has_no_import_side_effect_failure(self) -> None:
        spec = importlib.util.spec_from_file_location("check_rust_server_slice", CHECKER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.validate()
        self.assertGreaterEqual(result["source_tokens"], 20)
        self.assertTrue(result["authority_transferred_source_candidate"])


if __name__ == "__main__":
    unittest.main()
