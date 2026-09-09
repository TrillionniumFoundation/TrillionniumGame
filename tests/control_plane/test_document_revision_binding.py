"""Exact topic revision binding without weakening repository documentation checks."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "document_revision_checker", ROOT / "scripts/check-documentation-authority.py"
)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class DocumentRevisionBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.authority = {
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
                "repository_wide_markdown_allowlist": True,
                "every_rust_package_has_module_documentation": True,
            },
            "root_human_documents": ["CURRENT_PLAN.md"],
            "current_human_documents": sorted(CHECKER.EXPECTED_CURRENT),
            "generated_human_rollups": ["docs/development/FEATURE_PARITY_MATRIX.md"],
            "permitted_non_development_markdown": [],
            "machine_control_documents": ["docs/status/state.json"],
            "removed_human_document_roots": ["docs/legacy"],
            "module_document_registry": "docs/status/MODULE_DOCUMENTATION.json",
            "forbidden_human_filename_patterns": [r"_OLD\.md$"],
            "claims": {
                "documentation_consolidated": True,
                "historical_human_docs_removed_from_active_tree": True,
                "repository_markdown_allowlist_enforced": True,
                "module_documentation_registry_enforced": True,
                "machine_evidence_deleted": False,
                "compatibility_credit": False,
                "production_ready": False,
                "public_online": False,
                "nakama_retired": False,
            },
        }
        self.write(
            "CURRENT_PLAN.md",
            "# 开发计划 v3.1\ndocs/DOCUMENTATION_AUTHORITY.json\n历史信息只保留在 Git 历史\n",
        )
        for path in CHECKER.EXPECTED_CURRENT:
            self.write_topic(path, "2026-09-01")
        self.write("docs/development/FEATURE_PARITY_MATRIX.md", "# Fixture rollup\n")
        self.write("docs/status/state.json", "{}\n")
        module = "trnm-fixture"
        row = {
            "id": module,
            "path": f"crates/{module}",
            "manifest": f"crates/{module}/Cargo.toml",
            "documentation": f"crates/{module}/README.md",
            "workspace": "root",
            "lifecycle": "fixture",
            "maturity": "source-candidate",
            "owner_role": "test",
            "authority": "test-only",
            "blocking_gaps": ["GAP-P1-DOCS-001"],
            "claim_credit": False,
        }
        self.write(row["manifest"], '[package]\nname="trnm-fixture"\nversion="0.0.0"\n')
        self.write(
            row["documentation"],
            "# trnm-fixture\nStatus: **module documentation; test fixture**\n"
            + "\n".join(CHECKER.EXPECTED_MODULE_SECTIONS) + "\n" * 45,
        )
        registry = {
            "schema": "trillionnium.module-documentation.v1",
            "project_id": "trillionnium-game",
            "plan_version": 3,
            "required_sections": list(CHECKER.EXPECTED_MODULE_SECTIONS),
            "modules": [row],
            "summary": {
                "module_count": 1,
                "documented_count": 1,
                "root_workspace_count": 1,
                "isolated_workspace_count": 0,
                "undocumented_count": 0,
            },
        }
        self.write("docs/status/MODULE_DOCUMENTATION.json", json.dumps(registry))

    def write(self, path: str, text: str) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")

    def write_topic(self, path: str, revision: str) -> None:
        self.write(
            path,
            "# Test topic\n\n" + CHECKER.STATUS_MARKER
            + f"  \nRevision: {revision}\n" + "\n" * 22,
        )

    def validate(self) -> dict:
        self.write("docs/DOCUMENTATION_AUTHORITY.json", json.dumps(self.authority))
        return CHECKER.validate(self.root)

    def bind_updated_topics(self) -> None:
        self.authority["document_revisions"] = {
            "docs/ARCHITECTURE.md": "2026-09-03",
            "docs/DEVELOPMENT.md": "2026-09-03",
        }
        for path, revision in self.authority["document_revisions"].items():
            self.write_topic(path, revision)

    def test_v1_without_overrides_preserves_exact_baseline(self) -> None:
        result = self.validate()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(set(result["document_revisions"].values()), {"2026-09-01"})
        self.assertEqual(result["module_document_count"], 1)

    def test_updated_topics_pass_without_backdating_or_rewriting_other_topics(self) -> None:
        self.bind_updated_topics()
        result = self.validate()
        self.assertEqual(result["document_revisions"]["docs/ARCHITECTURE.md"], "2026-09-03")
        self.assertEqual(result["document_revisions"]["docs/COMPATIBILITY.md"], "2026-09-01")

    def test_unregistered_topic_revision_still_fails(self) -> None:
        self.write_topic("docs/ARCHITECTURE.md", "2026-09-03")
        with self.assertRaisesRegex(CHECKER.ValidationError, "Revision: 2026-09-01"):
            self.validate()

    def test_override_does_not_allow_stale_document_marker(self) -> None:
        self.authority["document_revisions"] = {"docs/ARCHITECTURE.md": "2026-09-03"}
        with self.assertRaisesRegex(CHECKER.ValidationError, "Revision: 2026-09-03"):
            self.validate()

    def test_override_keys_are_closed_world(self) -> None:
        for path in ["docs/unknown.md", "../docs/ARCHITECTURE.md", "/docs/ARCHITECTURE.md",
                     "docs//ARCHITECTURE.md", "CURRENT_PLAN.md", "crates/trnm-fixture/README.md"]:
            with self.subTest(path=path):
                self.authority["document_revisions"] = {path: "2026-09-03"}
                with self.assertRaisesRegex(CHECKER.ValidationError, "unregistered"):
                    self.validate()

    def test_override_requires_object_not_null_or_sequence(self) -> None:
        for value in [None, [], True, "2026-09-03", 3]:
            with self.subTest(value=value):
                self.authority["document_revisions"] = value
                with self.assertRaisesRegex(CHECKER.ValidationError, "must be an object"):
                    self.validate()

    def test_invalid_revision_values_are_rejected(self) -> None:
        for value in [None, True, 20260903, "2026-9-03", "2026-09-03x", "2026-02-30",
                      "2026-13-01", "2026-00-10", "2026-09-00", "２０２６-09-03"]:
            with self.subTest(value=value):
                self.authority["document_revisions"] = {"docs/ARCHITECTURE.md": value}
                with self.assertRaises(CHECKER.ValidationError):
                    self.validate()

    def test_override_cannot_predate_baseline(self) -> None:
        self.authority["document_revisions"] = {"docs/ARCHITECTURE.md": "2026-08-31"}
        with self.assertRaisesRegex(CHECKER.ValidationError, "predates"):
            self.validate()

    def test_calendar_validation_applies_to_global_baseline(self) -> None:
        self.authority["revision"] = "2026-02-29"
        with self.assertRaisesRegex(CHECKER.ValidationError, "calendar date"):
            self.validate()
        self.assertEqual(CHECKER.valid_revision("2028-02-29", "fixture"), "2028-02-29")

    def test_marker_requires_one_exact_whole_line(self) -> None:
        cases = ["", "Revision: 2026-09-010", "prefix Revision: 2026-09-01",
                 "Revision: 2026-09-01 extra", " Revision: 2026-09-01",
                 "Revision : 2026-09-01", "Revision: 2026-09-01\nRevision: 2026-09-01",
                 "Revision: 2026-09-01\n  Revision: 2026-09-03"]
        for marker in cases:
            with self.subTest(marker=marker):
                self.write("docs/ARCHITECTURE.md", "# Topic\n" + CHECKER.STATUS_MARKER
                           + "\n" + marker + "\n" * 25)
                with self.assertRaisesRegex(CHECKER.ValidationError, "exactly one Revision"):
                    self.validate()

    def test_markdown_trailing_spaces_and_crlf_are_supported(self) -> None:
        self.assertTrue(CHECKER.has_exact_revision_marker("Revision: 2026-09-01  \r\n", "2026-09-01"))
        self.assertTrue(CHECKER.has_exact_revision_marker("Revision: 2026-09-01\t\n", "2026-09-01"))

    def test_duplicate_json_keys_fail_instead_of_last_value_winning(self) -> None:
        for raw in ['{"revision":"2026-09-01","revision":"2026-09-03"}',
                    '{"document_revisions":{"docs/README.md":"2026-09-01","docs/README.md":"2026-09-03"}}']:
            with self.subTest(raw=raw):
                self.write("duplicate.json", raw)
                with self.assertRaisesRegex(CHECKER.ValidationError, "duplicate JSON key"):
                    CHECKER.load_object(self.root / "duplicate.json")

    def test_allowlist_remains_enforced_with_valid_revisions(self) -> None:
        self.bind_updated_topics()
        self.write("docs/UNDECLARED.md", "# Not registered\n")
        with self.assertRaisesRegex(CHECKER.ValidationError, "undeclared"):
            self.validate()

    def test_module_coverage_remains_enforced_with_valid_revisions(self) -> None:
        self.bind_updated_topics()
        (self.root / "crates/trnm-fixture/README.md").unlink()
        with self.assertRaisesRegex(CHECKER.ValidationError, "README is missing"):
            self.validate()

    def test_claim_promotion_remains_rejected_with_valid_revisions(self) -> None:
        self.bind_updated_topics()
        self.authority["claims"]["production_ready"] = True
        with self.assertRaisesRegex(CHECKER.ValidationError, "premature documentation claim"):
            self.validate()

    def test_broken_links_remain_rejected_with_valid_revisions(self) -> None:
        self.bind_updated_topics()
        path = self.root / "docs/ARCHITECTURE.md"
        path.write_text(path.read_text() + "\n[broken](MISSING.md)\n")
        with self.assertRaisesRegex(CHECKER.ValidationError, "broken current links"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
