from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs/status/COMPONENT_DOCUMENTATION.json"
AUTHORITY = ROOT / "docs/DOCUMENTATION_AUTHORITY.json"


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise TypeError(f"{path}: top-level value must be an object")
    return value


class ComponentDocumentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_object(REGISTRY)
        cls.authority = load_object(AUTHORITY)

    def test_registry_is_admitted_by_documentation_authority(self) -> None:
        self.assertIn(
            "docs/status/COMPONENT_DOCUMENTATION.json",
            self.authority["machine_control_documents"],
        )
        self.assertEqual(
            self.registry["schema"], "trillionnium.component-documentation.v1"
        )
        self.assertEqual(self.registry["project_id"], "trillionnium-game")
        self.assertEqual(self.registry["plan_version"], 3)

    def test_every_component_has_real_roots_documents_tests_and_boundaries(self) -> None:
        required = set(self.registry["required_fields"])
        components = self.registry["components"]
        self.assertEqual(len(components), len({row["id"] for row in components}))
        for component in components:
            with self.subTest(component=component["id"]):
                self.assertLessEqual(required, component.keys())
                self.assertRegex(component["id"], r"^COMPONENT-[A-Z0-9-]+$")
                self.assertTrue(component["owner_role"].strip())
                self.assertTrue(component["authority"].strip())
                self.assertTrue(component["architecture"].strip())
                self.assertTrue(component["failure_model"].strip())
                self.assertTrue(component["security_boundary"].strip())
                self.assertTrue(component["operations"].strip())
                self.assertTrue(component["known_gaps"])
                self.assertNotEqual(component["documentation_status"], "complete")
                for relative in component["roots"]:
                    self.assertTrue((ROOT / relative).exists(), relative)
                for relative in component["documentation"]:
                    self.assertTrue((ROOT / relative).is_file(), relative)
                for relative in component["test_entrypoints"]:
                    self.assertTrue((ROOT / relative).exists(), relative)

    def test_registry_covers_every_top_level_engineering_surface(self) -> None:
        covered = {
            relative
            for component in self.registry["components"]
            for relative in component["roots"]
        }
        required_roots = {
            "crates",
            "runtime",
            "migrations",
            "database/schema/v2",
            "contracts",
            "oracle",
            "scripts",
            "tools",
            ".github/workflows",
            "deploy",
            "docs/evidence",
            "docs/review",
            "docs/status",
        }
        self.assertLessEqual(required_roots, covered)

    def test_summary_is_exact_and_does_not_overclaim_depth(self) -> None:
        components = self.registry["components"]
        summary = self.registry["summary"]
        self.assertEqual(summary["component_count"], len(components))
        self.assertEqual(summary["fully_detailed_count"], 0)
        self.assertEqual(summary["partial_or_mixed_count"], len(components))
        self.assertEqual(summary["undocumented_count"], 0)
        self.assertFalse(summary["all_components_production_ready"])
        self.assertFalse(summary["all_components_independently_reviewed"])

    def test_control_and_governance_docs_do_not_claim_human_acceptance(self) -> None:
        by_id = {row["id"]: row for row in self.registry["components"]}
        control = by_id["COMPONENT-CONTROL-PLANE"]
        governance = by_id["COMPONENT-CI-GOVERNANCE"]
        evidence = by_id["COMPONENT-EVIDENCE-REVIEW"]
        self.assertIn("cannot grant human acceptance", control["authority"])
        self.assertIn("remain separate", governance["authority"])
        self.assertIn("cannot create", evidence["authority"])


if __name__ == "__main__":
    unittest.main()
