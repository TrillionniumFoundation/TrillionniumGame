from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-module-documentation-workspace.py"
SPEC = importlib.util.spec_from_file_location(
    "trnm_module_documentation_workspace", SCRIPT
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def write_fixture(
    root: Path,
    *,
    workspace: str,
    readme_sentence: str,
    register_as_member: bool,
) -> None:
    crate = root / "crates" / "trnm-fixture"
    crate.mkdir(parents=True)
    (crate / "README.md").write_text(
        "# trnm-fixture\n\n"
        f"Workspace class: `{workspace}`\n\n"
        "## Build and test\n\n"
        f"{readme_sentence}\n",
        encoding="utf-8",
    )
    (crate / "Cargo.toml").write_text(
        "[package]\nname = \"trnm-fixture\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    if register_as_member:
        members = '["crates/trnm-fixture"]'
        excluded = "[]"
    else:
        members = "[]"
        excluded = '["crates/trnm-fixture"]'
    (root / "Cargo.toml").write_text(
        f"[workspace]\nmembers = {members}\nexclude = {excluded}\n",
        encoding="utf-8",
    )
    registry = root / "docs" / "status"
    registry.mkdir(parents=True)
    (registry / "MODULE_DOCUMENTATION.json").write_text(
        json.dumps(
            {
                "schema": "trillionnium.module-documentation.v1",
                "modules": [
                    {
                        "id": "trnm-fixture",
                        "path": "crates/trnm-fixture",
                        "documentation": "crates/trnm-fixture/README.md",
                        "workspace": workspace,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class ModuleDocumentationWorkspaceTests(unittest.TestCase):
    def test_repository_workspace_documentation_is_consistent(self) -> None:
        result = VALIDATOR.validate(ROOT)
        self.assertEqual(result["status"], "verified")
        self.assertGreater(result["module_count"], 0)

    def test_root_module_cannot_claim_isolated_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(
                root,
                workspace="root",
                readme_sentence=(
                    "This isolated workspace is explicitly registered in package authority."
                ),
                register_as_member=True,
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "claims isolated workspace semantics"
            ):
                VALIDATOR.validate(root)

    def test_isolated_module_must_be_excluded_and_documented(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(
                root,
                workspace="isolated",
                readme_sentence=(
                    "This isolated workspace is explicitly registered in package authority."
                ),
                register_as_member=False,
            )
            result = VALIDATOR.validate(root)
            self.assertEqual(result["isolated_workspace_count"], 1)

    def test_registry_workspace_cannot_disagree_with_cargo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_fixture(
                root,
                workspace="isolated",
                readme_sentence=(
                    "This isolated workspace is explicitly registered in package authority."
                ),
                register_as_member=True,
            )
            with self.assertRaisesRegex(
                VALIDATOR.ValidationError, "isolated module missing from workspace.exclude"
            ):
                VALIDATOR.validate(root)


if __name__ == "__main__":
    unittest.main()
