from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit-workflow-pins.py"


class WorkflowPinAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("audit_workflow_pins", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_action_reference_classification(self) -> None:
        self.assertEqual(self.module.classify_use("./.github/actions/local"), "local")
        self.assertEqual(
            self.module.classify_use(
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
            ),
            "immutable-sha",
        )
        self.assertEqual(self.module.classify_use("actions/checkout@v4"), "mutable-ref")
        self.assertEqual(self.module.classify_use("actions/checkout"), "missing-ref")

    def test_image_reference_classification(self) -> None:
        self.assertEqual(
            self.module.classify_image(
                "postgres:17.6@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94"
            ),
            "immutable-digest",
        )
        self.assertEqual(self.module.classify_image("postgres:17.6"), "mutable-image")

    def test_repository_audit_is_nonempty_and_fail_closed(self) -> None:
        result = self.module.audit()
        self.assertGreater(result["reference_count"], 0)
        self.assertEqual(result["problem_count"], len(result["problems"]))
        self.assertFalse(result["claims"]["actions_enabled"])
        self.assertFalse(result["claims"]["dependencies_reviewed"])
        self.assertFalse(result["claims"]["supply_chain_gate_complete"])


if __name__ == "__main__":
    unittest.main()
