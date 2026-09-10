from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-documentation-authority.py"
SOCIAL_ID = "trnm-social-core"
SOCIAL_PATH = "crates/trnm-social-core"


def load_checker():
    spec = importlib.util.spec_from_file_location("documentation_authority", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SocialModuleRegistrationTests(unittest.TestCase):
    def test_social_module_is_bound_in_every_current_inventory(self) -> None:
        cargo = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
        self.assertEqual(cargo["workspace"]["members"].count(SOCIAL_PATH), 1)

        package = json.loads(
            (ROOT / "docs/development/RUST_PACKAGE_AUTHORITY.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(package["workspace"]["members"].count(SOCIAL_PATH), 1)

        modules = json.loads(
            (ROOT / "docs/status/MODULE_DOCUMENTATION.json").read_text(
                encoding="utf-8"
            )
        )
        module_rows = [row for row in modules["modules"] if row.get("id") == SOCIAL_ID]
        self.assertEqual(len(module_rows), 1)
        self.assertEqual(module_rows[0]["path"], SOCIAL_PATH)
        self.assertEqual(module_rows[0]["manifest"], f"{SOCIAL_PATH}/Cargo.toml")
        self.assertEqual(module_rows[0]["documentation"], f"{SOCIAL_PATH}/README.md")
        self.assertFalse(module_rows[0]["claim_credit"])

        implementation = json.loads(
            (ROOT / "docs/status/IMPLEMENTATION_INVENTORY.json").read_text(
                encoding="utf-8"
            )
        )
        component_rows = [
            row
            for row in implementation["components"]
            if row.get("id") == "COMP-SOCIAL-CORE"
        ]
        self.assertEqual(len(component_rows), 1)
        self.assertEqual(component_rows[0]["path"], SOCIAL_PATH)
        self.assertFalse(component_rows[0]["claim_credit"])

        state = json.loads(
            (ROOT / "docs/status/CURRENT_STATE.json").read_text(encoding="utf-8")
        )
        self.assertIs(state["implementation"]["social_core_source_candidate"], True)

    def _minimal_root(self, directory: str):
        root = Path(directory)
        target = root / SOCIAL_PATH
        target.mkdir(parents=True)
        shutil.copy2(ROOT / SOCIAL_PATH / "Cargo.toml", target / "Cargo.toml")
        shutil.copy2(ROOT / SOCIAL_PATH / "README.md", target / "README.md")

        registry = json.loads(
            (ROOT / "docs/status/MODULE_DOCUMENTATION.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(row for row in registry["modules"] if row["id"] == SOCIAL_ID)
        registry["modules"] = [copy.deepcopy(row)]
        registry["summary"] = {
            "module_count": 1,
            "documented_count": 1,
            "root_workspace_count": 1,
            "isolated_workspace_count": 0,
            "undocumented_count": 0,
        }
        registry_path = root / "docs/status/MODULE_DOCUMENTATION.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        return root, registry_path, registry

    def test_social_registration_removal_and_path_substitution_fail_closed(self) -> None:
        checker = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root, registry_path, registry = self._minimal_root(directory)
            documented, summary = checker.validate_module_registry(root, registry_path)
            self.assertEqual(len(documented), 1)
            self.assertEqual(summary["module_count"], 1)

            removed = copy.deepcopy(registry)
            removed["modules"] = []
            removed["summary"].update(
                module_count=0,
                documented_count=0,
                root_workspace_count=0,
                undocumented_count=0,
            )
            registry_path.write_text(
                json.dumps(removed, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                checker.ValidationError, "module registry must contain modules"
            ):
                checker.validate_module_registry(root, registry_path)

            substituted = copy.deepcopy(registry)
            substituted["modules"][0]["path"] = "crates/trnm-identity-core"
            registry_path.write_text(
                json.dumps(substituted, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                checker.ValidationError, "path must be crates/trnm-social-core"
            ):
                checker.validate_module_registry(root, registry_path)


if __name__ == "__main__":
    unittest.main()
