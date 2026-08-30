from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-denominator-materialization.py"


class DenominatorMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("check_denominator_materialization", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_absent_materialization_is_explicitly_non_creditable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(self.module, "ROOT", root), mock.patch.object(
                self.module, "STATUS", root / "status.json"
            ), mock.patch.object(
                self.module, "DRAFT", root / "draft.json"
            ), mock.patch.object(
                self.module, "WORKLIST", root / "worklist.json"
            ):
                result = self.module.validate()
        self.assertFalse(result["present"])
        self.assertFalse(result["review_ready"])
        self.assertEqual(set(result["missing_denominators"]), self.module.EXPECTED)

    def test_true_product_claim_is_rejected_recursively(self) -> None:
        with self.assertRaises(self.module.ContractError):
            self.module.reject_true_claims(
                {"nested": {"compatibility_credit": True}}, "fixture"
            )

    def test_complete_pending_packet_is_review_ready_but_not_sg1(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = []
            for identifier in sorted(self.module.EXPECTED):
                relative = f"manifests/{identifier}.json"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                manifest = {
                    "denominator": identifier,
                    "leaf_count": 1,
                    "leaves": [
                        {
                            "id": f"{identifier}-1",
                            "classification": "mandatory",
                            "owner_role": "protocol",
                            "task_ids": ["TG-W0-002"],
                            "test_ids": ["TG-DIFF-1"],
                        }
                    ],
                    "claims": {
                        "sg1_eligible": False,
                        "compatibility_credit": False,
                        "production_ready": False,
                    },
                }
                path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                data = path.read_bytes()
                rows.append(
                    {
                        "id": identifier,
                        "manifest": {
                            "path": relative,
                            "sha256": self.module.sha256(data),
                            "size_bytes": len(data),
                        },
                        "leaf_count": 1,
                        "classified_count": 1,
                        "unclassified_count": 0,
                        "owner_bound_count": 1,
                        "task_bound_count": 1,
                        "test_bound_count": 1,
                        "review_status": "pending-independent-review",
                    }
                )
            aggregate = {
                "denominator_count": len(rows),
                "leaf_count_total": len(rows),
                "classified_count": len(rows),
                "unclassified_count": 0,
                "owner_bound_count": len(rows),
                "task_bound_count": len(rows),
                "test_bound_count": len(rows),
                "manifest_sha256": "0" * 64,
            }
            draft = {
                "schema": "trillionnium.denominator-review-packet.draft.v1",
                "project_id": "trillionnium-game",
                "denominators": rows,
                "missing_denominators": [],
                "aggregate": aggregate,
                "review": {
                    "decision": "pending",
                    "independent": False,
                    "minimum_reviewers": 2,
                },
                "claims": {
                    "sg1_eligible": False,
                    "compatibility_credit": False,
                    "production_ready": False,
                },
            }
            status = {
                "schema": "trillionnium.denominator-materialization.v1",
                "project_id": "trillionnium-game",
                "status": "review-ready",
                "materialized_denominator_count": len(rows),
                "materialized_denominators": sorted(self.module.EXPECTED),
                "missing_denominators": [],
                "claims": draft["claims"],
            }
            status_path = root / "status.json"
            draft_path = root / "draft.json"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            draft_path.write_text(json.dumps(draft), encoding="utf-8")
            with mock.patch.object(self.module, "ROOT", root), mock.patch.object(
                self.module, "STATUS", status_path
            ), mock.patch.object(self.module, "DRAFT", draft_path):
                result = self.module.validate()
        self.assertTrue(result["present"])
        self.assertTrue(result["review_ready"])
        self.assertFalse(result["claims"]["sg1_eligible"])
        self.assertFalse(result["claims"]["compatibility_credit"])

    def test_repository_packet_never_overclaims(self) -> None:
        result = self.module.validate()
        if result["present"]:
            self.assertFalse(result["claims"]["sg1_eligible"])
            self.assertFalse(result["claims"]["compatibility_credit"])
            self.assertFalse(result["claims"]["production_ready"])


if __name__ == "__main__":
    unittest.main()
