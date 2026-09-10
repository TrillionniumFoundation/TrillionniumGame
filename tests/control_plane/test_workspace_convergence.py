from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-workspace-convergence.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("workspace_convergence", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryCheckerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        for relative in (
            "Cargo.toml",
            "CURRENT_PLAN.md",
            "crates/trnm-server/README.md",
            "docs/status/MODULE_DOCUMENTATION.json",
            "docs/status/IMPLEMENTATION_INVENTORY.json",
            "docs/status/CURRENT_STATE.json",
            "docs/status/RUST_SERVER_STATUS.json",
            "docs/development/RUST_PACKAGE_AUTHORITY.json",
        ):
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for manifest in (ROOT / "crates").glob("*/Cargo.toml"):
            target = self.root / manifest.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest, target)
        self.checker = load_checker()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_repository_checker(self) -> None:
        subprocess.run(["python3", str(SCRIPT)], cwd=ROOT, check=True)

    def test_stale_plan_authority_is_rejected(self) -> None:
        plan = self.root / "CURRENT_PLAN.md"
        text = plan.read_text(encoding="utf-8").replace(
            "`crates/trnm-server` 现在是唯一默认 `trnm-server` composition authority",
            "当前 canonical database-backed server 仍位于 `crates/trnm-persistence-pg`",
        )
        plan.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(
            self.checker.ConvergenceError, "CURRENT_PLAN server authority is stale"
        ):
            self.checker.validate(self.root)

    def test_registry_cannot_demote_canonical_server(self) -> None:
        path = self.root / "docs/status/MODULE_DOCUMENTATION.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        row = next(item for item in value["modules"] if item["id"] == "trnm-server")
        row["authority"] = "foundation process prototype; not the canonical production binary"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(
            self.checker.ConvergenceError, "module registry server authority is stale"
        ):
            self.checker.validate(self.root)

    def test_diagnostic_binary_must_remain_feature_gated(self) -> None:
        path = self.root / "crates/trnm-persistence-pg/Cargo.toml"
        text = path.read_text(encoding="utf-8").replace(
            'required-features = ["diagnostic-compat-server"]\n', ""
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(
            self.checker.ConvergenceError, "diagnostic server is not feature gated"
        ):
            self.checker.validate(self.root)


if __name__ == "__main__":
    unittest.main()
