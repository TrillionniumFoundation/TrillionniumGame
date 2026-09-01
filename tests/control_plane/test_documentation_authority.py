from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/check-documentation-authority.py"
CURRENT = [
    "docs/README.md",
    "docs/ARCHITECTURE.md",
    "docs/DEVELOPMENT.md",
    "docs/COMPATIBILITY.md",
    "docs/TESTING_AND_EVIDENCE.md",
    "docs/SECURITY_AND_PRIVACY.md",
    "docs/OPERATIONS_AND_RELEASE.md",
    "docs/GOVERNANCE.md",
    "docs/ROADMAP.md",
]
ROOT_DOCS = [
    "README.md",
    "CURRENT_PLAN.md",
    "PROJECT_BOUNDARY.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "AGENTS.md",
]


def load_checker():
    spec = importlib.util.spec_from_file_location("check_documentation_authority", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DocumentationAuthorityTests(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        authority = {
            "schema": "trillionnium.documentation-authority.v1",
            "project_id": "trillionnium-game",
            "plan_version": 3,
            "revision": "2026-09-01",
            "policy": {
                "single_current_human_document_per_topic": True,
                "historical_markdown_allowed_in_active_tree": False,
                "git_history_is_the_human_document_archive": True,
                "machine_evidence_may_be_immutable_and_dated": True,
                "broken_repository_document_references_allowed": False,
                "legacy_document_names_allowed": False,
            },
            "root_human_documents": ROOT_DOCS,
            "current_human_documents": CURRENT,
            "generated_human_rollups": ["docs/development/FEATURE_PARITY_MATRIX.md"],
            "machine_control_documents": ["docs/status/CURRENT_STATE.json"],
            "machine_record_roots": ["docs/status", "docs/development"],
            "removed_human_document_roots": [
                "docs/adr",
                "docs/architecture",
                "docs/audit",
                "docs/operations",
                "docs/release",
                "docs/security",
                "docs/testing",
            ],
            "forbidden_human_filename_patterns": [
                r"(?:^|/)[0-9]{4}-[0-9]{2}-[0-9]{2}[^/]*\.md$",
                r"(?:^|/)[^/]*(?:_V[0-9]+|_ALPHA|_CANDIDATE|_SUPERSEDED)[^/]*\.md$",
                r"(?:^|/)README_[^/]+\.md$",
            ],
            "claims": {
                "documentation_consolidated": True,
                "historical_human_docs_removed_from_active_tree": True,
                "machine_evidence_deleted": False,
                "compatibility_credit": False,
                "production_ready": False,
                "public_online": False,
                "nakama_retired": False,
            },
        }
        for value in ROOT_DOCS:
            path = root / value
            path.parent.mkdir(parents=True, exist_ok=True)
            if value == "CURRENT_PLAN.md":
                path.write_text(
                    "# TrillionniumGame 全量 Rust 重写开发计划 v3.1\n\n"
                    "docs/DOCUMENTATION_AUTHORITY.json\n"
                    "历史信息只保留在 Git 历史。\n",
                    encoding="utf-8",
                )
            else:
                path.write_text(f"# {path.stem}\n", encoding="utf-8")
        for value in CURRENT:
            path = root / value
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                f"# {path.stem}",
                "",
                "Status: **authoritative current documentation**",
                "Revision: 2026-09-01",
                "",
            ] + [f"line {index}" for index in range(20)]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rollup = root / "docs/development/FEATURE_PARITY_MATRIX.md"
        rollup.parent.mkdir(parents=True, exist_ok=True)
        rollup.write_text("# generated\n", encoding="utf-8")
        machine = root / "docs/status/CURRENT_STATE.json"
        machine.parent.mkdir(parents=True, exist_ok=True)
        machine.write_text("{}\n", encoding="utf-8")
        authority_path = root / "docs/DOCUMENTATION_AUTHORITY.json"
        authority_path.write_text(json.dumps(authority, indent=2) + "\n", encoding="utf-8")
        return authority_path

    def test_repository_authority_passes(self) -> None:
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
        self.assertEqual(result["current_human_document_count"], 9)
        self.assertEqual(result["historical_markdown_count"], 0)
        self.assertGreater(result["active_reference_surface_count"], 0)

    def test_extra_legacy_markdown_is_rejected(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            legacy = root / "docs/architecture/OLD_V1.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("# old\n", encoding="utf-8")
            with self.assertRaisesRegex(module.ValidationError, "removed human documentation root"):
                module.validate(root, authority)

    def test_broken_current_markdown_link_is_rejected(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            path = root / "docs/ARCHITECTURE.md"
            path.write_text(path.read_text(encoding="utf-8") + "[missing](NOPE.md)\n", encoding="utf-8")
            with self.assertRaisesRegex(module.ValidationError, "broken local link"):
                module.validate(root, authority)

    def test_active_script_reference_to_removed_doc_is_rejected(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            script = root / "scripts/check.py"
            script.parent.mkdir(parents=True)
            script.write_text('PATH = "docs/legacy.md"\n', encoding="utf-8")
            with self.assertRaisesRegex(
                module.ValidationError,
                "active files reference removed documentation",
            ):
                module.validate(root, authority)

    def test_active_source_reference_to_removed_doc_is_rejected(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            source = root / "runtime/current.go"
            source.parent.mkdir(parents=True)
            source.write_text('const guide = "docs/architecture/OLD.md"\n', encoding="utf-8")
            with self.assertRaisesRegex(
                module.ValidationError,
                "active files reference removed documentation",
            ):
                module.validate(root, authority)

    def test_machine_control_reference_to_removed_doc_is_rejected(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            machine = root / "docs/status/CURRENT_STATE.json"
            machine.write_text(
                '{"current_guide":"docs/architecture/OLD.md"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                module.ValidationError,
                "active files reference removed documentation",
            ):
                module.validate(root, authority)

    def test_test_fixture_reference_is_not_an_active_authority(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            fixture = root / "tests/fixtures/legacy.py"
            fixture.parent.mkdir(parents=True)
            fixture.write_text('REMOVED = "docs/legacy.md"\n', encoding="utf-8")
            result = module.validate(root, authority)
            self.assertEqual(result["historical_markdown_count"], 0)

    def test_historical_evidence_reference_is_not_current_authority(self) -> None:
        module = load_checker()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = self.fixture(root)
            evidence = root / "docs/evidence/old.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"old":"docs/legacy.md"}\n', encoding="utf-8")
            result = module.validate(root, authority)
            self.assertEqual(result["historical_markdown_count"], 0)


if __name__ == "__main__":
    unittest.main()
