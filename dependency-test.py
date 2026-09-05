"""Pin alignment regressions; source checks are not crypto acceptance."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-rust-foundation.py"
ADAPTER = "crates/trnm-persistence-pg"


def load_checker():
    spec = importlib.util.spec_from_file_location("trnm_foundation_dependency_alignment", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("foundation validator unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FoundationDependencyAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()
        cls.manifest = tomllib.loads((ROOT / ADAPTER / "Cargo.toml").read_text(encoding="utf-8"))

    def reject(self, dependencies, member=ADAPTER):
        # A negative mutation must not pass only because its baseline is broken.
        self.checker.validate_member(member)
        manifest = tomllib.loads((ROOT / member / "Cargo.toml").read_text(encoding="utf-8"))
        manifest["dependencies"] = dependencies
        with patch.object(self.checker, "load_toml", return_value=manifest):
            with self.assertRaisesRegex(SystemExit, "dependency allowlist mismatch"):
                self.checker.validate_member(member)

    def test_actual_adapter_manifest_matches_closed_set(self):
        expected = self.checker.EXPECTED_DEPENDENCIES[ADAPTER]
        self.assertEqual(expected["openssl"], "=0.10.81")
        self.assertEqual(self.manifest["dependencies"], expected)

    def test_existing_openssl_has_exact_locked_registry_version(self):
        lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
        selected = [row for row in lock["package"] if row["name"] == "openssl"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["version"], "0.10.81")
        self.assertEqual(selected[0]["source"], "registry+https://github.com/rust-lang/crates.io-index")
        self.assertRegex(selected[0]["checksum"], r"^[0-9a-f]{64}$")

    def test_missing_dependency_rejected(self):
        deps = deepcopy(self.manifest["dependencies"])
        del deps["openssl"]
        self.reject(deps)

    def test_range_and_alternate_source_rejected(self):
        for replacement in ("0.10.81", "^0.10.81", "*", "=0.10.80",
                            {"git": "https://example.invalid/openssl"},
                            {"version": "=0.10.81", "features": ["vendored"]}):
            with self.subTest(replacement=replacement):
                deps = deepcopy(self.manifest["dependencies"])
                deps["openssl"] = replacement
                self.reject(deps)

    def test_unknown_dependency_rejected(self):
        deps = deepcopy(self.manifest["dependencies"])
        deps["unlisted-dependency"] = "=1.0.0"
        self.reject(deps)

    def test_other_pinned_dependency_change_rejected(self):
        deps = deepcopy(self.manifest["dependencies"])
        deps["postgres"] = "0.19"
        self.reject(deps)

    def test_runtime_feature_expansion_rejected(self):
        deps = deepcopy(self.manifest["dependencies"])
        deps["tokio"]["features"].append("full")
        self.reject(deps)

    def test_openssl_in_pure_core_rejected(self):
        for member in sorted(self.checker.PURE_CORE_MEMBERS):
            with self.subTest(member=member):
                deps = deepcopy(self.checker.EXPECTED_DEPENDENCIES[member])
                deps["openssl"] = "=0.10.81"
                self.reject(deps, member)

    def test_actual_validator_returns_nonempty_sources(self):
        sources, tests = self.checker.validate_member(ADAPTER)
        self.assertTrue(sources)
        self.assertTrue(tests)
        self.assertIn(ROOT / ADAPTER / "src/lib.rs", sources)

    def test_complete_repository_checker_passes(self):
        result = subprocess.run([sys.executable, str(CHECKER)], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rust-foundation-workspace-contract-passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
