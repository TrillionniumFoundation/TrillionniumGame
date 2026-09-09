"""Real server-checker coverage; passing source checks is not runtime acceptance."""
from __future__ import annotations

import ast
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-trnm-server.py"
FOUNDATION = ROOT / "scripts/check-rust-foundation.py"
MANIFEST = ROOT / "crates/trnm-persistence-pg/Cargo.toml"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("test checker loader is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServerDependencyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load(CHECKER, "trnm_server_dependency_test")
        cls.foundation = load(FOUNDATION, "trnm_foundation_dependency_test")
        cls.manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    def reject(self, section, mutate):
        self.server.validate_dependency_boundary(deepcopy(self.manifest))
        changed = deepcopy(self.manifest)
        mutate(changed[section])
        self.assertNotEqual(changed, self.manifest, "hostile fixture must change its target")
        with self.assertRaisesRegex(SystemExit, "dependency boundary"):
            self.server.validate_dependency_boundary(changed)

    def test_actual_manifest_passes_shared_boundary(self):
        self.server.validate_dependency_boundary(deepcopy(self.manifest))

    def test_server_and_foundation_use_identical_exact_table(self):
        actual = self.server.expected_persistence_dependencies()
        self.assertEqual(actual, self.foundation.EXPECTED_DEPENDENCIES["crates/trnm-persistence-pg"])
        self.assertEqual(actual["openssl"], "=0.10.81")
        self.assertEqual(actual, self.manifest["dependencies"])

    def test_policy_is_a_deep_copy(self):
        first = self.server.expected_persistence_dependencies()
        first["tokio"]["features"].append("full")
        first.pop("openssl")
        self.assertEqual(self.server.expected_persistence_dependencies(), self.manifest["dependencies"])

    def test_missing_openssl_rejects(self):
        self.reject("dependencies", lambda d: d.pop("openssl"))

    def test_unknown_dependency_rejects(self):
        self.reject("dependencies", lambda d: d.update({"unknown": "=1.0.0"}))

    def test_version_ranges_and_wrong_pin_reject(self):
        for value in ("0.10.81", "^0.10.81", "*", "=0.10.80"):
            with self.subTest(value=value):
                self.reject("dependencies", lambda d: d.update(openssl=value))

    def test_alternate_sources_and_dependency_alias_reject(self):
        values = ({"git": "https://example.invalid/openssl"}, {"path": "../openssl"},
                  {"version": "=0.10.81", "registry": "other"},
                  {"version": "=0.10.81", "package": "other"})
        for value in values:
            with self.subTest(value=value):
                self.reject("dependencies", lambda d: d.update(openssl=value))

    def test_dependency_features_or_optional_mode_reject(self):
        for value in ({"version": "=0.10.81", "features": ["vendored"]},
                      {"version": "=0.10.81", "optional": True},
                      {"version": "=0.10.81", "default-features": False}):
            with self.subTest(value=value):
                self.reject("dependencies", lambda d: d.update(openssl=value))

    def test_other_runtime_pins_remain_exact(self):
        self.reject("dependencies", lambda d: d.update(postgres="0.19"))

    def test_runtime_features_remain_exact(self):
        self.reject("dependencies", lambda d: d["tokio"]["features"].append("full"))

    def test_internal_dependency_paths_remain_exact(self):
        self.reject("dependencies", lambda d: d.update({"trnm-contracts": {"path": "../../foreign"}}))

    def test_build_dependencies_remain_closed(self):
        for mutate in (lambda d: d.pop("prost-build"),
                       lambda d: d.update({"unknown-build": "=1.0.0"}),
                       lambda d: d.update({"prost-build": "0.14"})):
            with self.subTest(mutate=mutate):
                self.reject("build-dependencies", mutate)

    def test_missing_dependency_sections_reject(self):
        for section in ("dependencies", "build-dependencies"):
            with self.subTest(section=section):
                data = deepcopy(self.manifest)
                del data[section]
                with self.assertRaisesRegex(SystemExit, "dependency boundary"):
                    self.server.validate_dependency_boundary(data)

    def test_missing_sibling_policy_has_no_fallback(self):
        with TemporaryDirectory() as directory:
            with patch.object(self.server, "__file__", str(Path(directory) / "check-trnm-server.py")):
                with self.assertRaisesRegex(SystemExit, "policy could not be loaded"):
                    self.server.expected_persistence_dependencies()

    def test_malformed_or_empty_sibling_policy_has_no_fallback(self):
        sources = ("", "EXPECTED_DEPENDENCIES = {}", "EXPECTED_DEPENDENCIES = []",
                   "EXPECTED_DEPENDENCIES = {'crates/trnm-persistence-pg': {}}",
                   "EXPECTED_DEPENDENCIES = {'crates/trnm-persistence-pg': []}", "syntax = (")
        for source in sources:
            with self.subTest(source=source), TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "check-rust-foundation.py").write_text(source, encoding="utf-8")
                with patch.object(self.server, "__file__", str(path / "check-trnm-server.py")):
                    with self.assertRaisesRegex(SystemExit, "dependency policy"):
                        self.server.expected_persistence_dependencies()

    def test_cwd_cannot_shadow_sibling_policy(self):
        with TemporaryDirectory() as directory:
            fake = Path(directory) / "check-rust-foundation.py"
            fake.write_text("raise RuntimeError('cwd shadow executed')", encoding="utf-8")
            result = subprocess.run([sys.executable, str(CHECKER)], cwd=directory,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "trnm-server-source-contract-passed")

    def test_main_calls_shared_boundary_without_a_duplicate_literal(self):
        tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "validate_dependency_boundary"]
        self.assertEqual(len(calls), 1)
        boundary = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "validate_dependency_boundary")
        assignments = [n for n in ast.walk(boundary) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "expected_dependencies" for t in n.targets)]
        self.assertEqual(len(assignments), 1)
        self.assertIsInstance(assignments[0].value, ast.Call)
        self.assertEqual(assignments[0].value.func.id, "expected_persistence_dependencies")

    def test_real_main_rejects_hostile_dependency_manifest(self):
        changed = deepcopy(self.manifest)
        changed["dependencies"]["unknown"] = "=1.0.0"
        with patch.object(self.server.tomllib, "loads", return_value=changed):
            with self.assertRaisesRegex(SystemExit, "reviewed persistence dependency boundary"):
                self.server.main()

    def test_real_main_still_rejects_missing_required_source(self):
        missing = ROOT / "crates/trnm-persistence-pg/src/absent-contract-fixture.rs"
        self.assertFalse(missing.exists())
        with patch.object(self.server, "REQUIRED_FILES", self.server.REQUIRED_FILES | {missing}):
            with self.assertRaisesRegex(SystemExit, "missing files"):
                self.server.main()

    def test_real_main_still_rejects_omitted_workflow_invocation(self):
        original = Path.read_text
        workflow = ROOT / ".github/workflows/trillionnium-game-merge-gate.yml"
        def replaced(path, *args, **kwargs):
            text = original(path, *args, **kwargs)
            if path == workflow:
                text = text.replace("python3 scripts/check-trnm-server.py", "echo omitted-server-check")
            return text
        with patch.object(Path, "read_text", replaced):
            with self.assertRaisesRegex(SystemExit, "does not execute the server source contract"):
                self.server.main()

    def test_complete_real_server_checker_is_nonempty_and_noncreditable(self):
        result = subprocess.run([sys.executable, str(CHECKER)], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "trnm-server-source-contract-passed")
        self.assertGreater(report["source_files"], 20)
        self.assertGreaterEqual(report["source_tests"], 72)
        for field in ("cargo_executed_here", "live_database_executed_here", "compatibility_credit", "sg4_complete"):
            self.assertIs(report[field], False)


if __name__ == "__main__":
    unittest.main()
