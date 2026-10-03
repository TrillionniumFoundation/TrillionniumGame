from __future__ import annotations

from copy import deepcopy
import importlib.util
import tomllib
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
        self.assertEqual(result["runtime_module_count"], 37)
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
                        "legacy_repository.rs", "legacy_repository_tests.rs", "legacy_uuid.rs",
                        "legacy_http_api.rs", "legacy_http_api_tests.rs",
                        "legacy_config.rs", "auth_runtime.rs", "auth_app_tests.rs"):
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


    def test_standalone_base64_edge_is_exact_and_build_policy_stays_closed(self) -> None:
        spec = importlib.util.spec_from_file_location("server_source_dependency_boundary", SOURCE_CHECKER)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        manifest = tomllib.loads((ROOT / "crates/trnm-server/Cargo.toml").read_text())
        module.validate_server_dependencies(manifest)
        self.assertEqual(module.EXPECTED_DEPENDENCIES["base64"], "=0.22.1")
        for value in (None, "0.22.1", "=0.22.0", {"version":"=0.22.1","features":["alloc"]},
                      {"path":"../base64"}, {"version":"=0.22.1","package":"other"}):
            changed = deepcopy(manifest)
            if value is None: changed["dependencies"].pop("base64")
            else: changed["dependencies"]["base64"] = value
            with self.subTest(base64=value), self.assertRaises(module.ValidationError):
                module.validate_server_dependencies(changed)
        for section, name, value in (("dependencies","unreviewed","=1.0.0"),
                                      ("dependencies","trnm-contracts",{"path":"../../other"}),
                                      ("build-dependencies","base64","=0.22.1"),
                                      ("build-dependencies","prost-build","^0.14.3")):
            changed = deepcopy(manifest);changed[section][name] = value
            with self.subTest(section=section,name=name), self.assertRaises(module.ValidationError):
                module.validate_server_dependencies(changed)
        # No persistence policy expansion accompanies the server direct edge.
        persistence = tomllib.loads((ROOT / "crates/trnm-persistence-pg/Cargo.toml").read_text())
        self.assertNotIn("base64", persistence["dependencies"])

    def test_schema_target_is_documented_without_minting_an_app_route(self) -> None:
        spec = importlib.util.spec_from_file_location("schema_target_documented_interface", ROOT / "scripts/engineering_readiness.py")
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        config = (ROOT / "crates/trnm-server/src/runtime/config.rs").read_text()
        app = (ROOT / "crates/trnm-server/src/runtime/app.rs").read_text()
        wire = (ROOT / "crates/trnm-server/src/runtime/legacy_http_api.rs").read_text()
        interface = module.source_interface(config, app, list_integrated=True, legacy_http_source=wire)
        document = (ROOT / "docs/DEVELOPMENT.md").read_text()
        module.check_documented_interface(interface, document)
        self.assertIn("TRNM_SERVER_SCHEMA_TARGET", interface["environment_names"])
        # Schema configuration does not invent routes. These three endpoints
        # are present only through the actual closed Legacy dispatcher source.
        self.assertEqual({route for route in interface["routes"] if route[1].startswith("/v2/account/") or route[1] == "/v2/session/logout"}, {
            ("POST", "/v2/account/authenticate/device"),
            ("POST", "/v2/account/session/refresh"),
            ("POST", "/v2/session/logout"),
        })
        self.assertNotIn(("POST", "/v2/accounts/schema5"), interface["routes"])
        row = next(line for line in document.splitlines() if line.startswith('| `TRNM_SERVER_SCHEMA_TARGET` |'))
        self.assertIn('`storage-v4`', row);self.assertIn('`nakama-accounts-v5`', row)
        for changed in (document.replace(row+'\n',''), document.replace(row, row+'\n'+row),
                        document.replace(row,row.replace('TRNM_SERVER_SCHEMA_TARGET','TRNM_SERVER_SCHEMA_UNREVIEWED'))):
            with self.assertRaises(module.ValidationError):
                module.check_documented_interface(interface, changed)

if __name__ == "__main__":
    unittest.main()
