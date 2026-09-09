from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/check-evidence-index.py"
COMMIT = "a" * 40
TREE = "b" * 40


def load_module():
    spec = importlib.util.spec_from_file_location("check_evidence_index", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review(**overrides):
    value = {
        "decision": "accepted",
        "reviewer_identity": "independent-reviewer",
        "independent": True,
        "self_review": False,
        "reviewed_at": "2026-09-02T00:00:00Z",
        "reviewed_commit": COMMIT,
        "reviewed_tree": TREE,
    }
    value.update(overrides)
    return value


class EvidenceIndexContractTests(unittest.TestCase):
    def test_index_is_structurally_valid_and_credit_is_explicit(self) -> None:
        result = load_module().validate()
        self.assertEqual(
            result["schema"], "trillionnium.evidence-index-validation.v1"
        )
        self.assertGreaterEqual(result["evidence_count"], 0)
        self.assertEqual(
            result["evidence_count"],
            result["credited"] + result["diagnostic_only"],
        )

    def test_current_relay_evidence_cannot_gain_credit_implicitly(self) -> None:
        module = load_module()
        index = module.load_object(module.INDEX_PATH)
        for row in module.rows(index):
            if (
                row.get("evidence_id")
                == "TG-EV-RELAY-FOUNDATION-DATABASE-20260828"
            ):
                self.assertFalse(module.credit_enabled(row))
                break
        else:
            self.fail("expected relay foundation database evidence to be indexed")

    def test_exact_independent_review_is_accepted(self) -> None:
        module = load_module()
        row = {"review": review()}
        self.assertIsNotNone(
            module.accepted_review(
                row,
                target_commit=COMMIT,
                target_tree=TREE,
            )
        )

    def test_independent_true_self_review_true_is_rejected(self) -> None:
        module = load_module()
        row = {"review": review(self_review=True)}
        self.assertIsNone(
            module.accepted_review(
                row,
                target_commit=COMMIT,
                target_tree=TREE,
            )
        )

    def test_independent_false_self_review_false_is_rejected(self) -> None:
        module = load_module()
        row = {"review": review(independent=False)}
        self.assertIsNone(
            module.accepted_review(
                row,
                target_commit=COMMIT,
                target_tree=TREE,
            )
        )

    def test_missing_independence_boolean_is_rejected(self) -> None:
        module = load_module()
        candidate = review()
        candidate.pop("independent")
        row = {"review": candidate}
        self.assertIsNone(
            module.accepted_review(
                row,
                target_commit=COMMIT,
                target_tree=TREE,
            )
        )

    def test_blank_or_noncanonical_reviewer_identity_is_rejected(self) -> None:
        module = load_module()
        for identity in ("", "   ", " reviewer "):
            with self.subTest(identity=identity):
                row = {"review": review(reviewer_identity=identity)}
                self.assertIsNone(
                    module.accepted_review(
                        row,
                        target_commit=COMMIT,
                        target_tree=TREE,
                    )
                )

    def test_stale_review_commit_or_tree_is_rejected(self) -> None:
        module = load_module()
        for overrides in (
            {"reviewed_commit": "c" * 40},
            {"reviewed_tree": "d" * 40},
        ):
            with self.subTest(overrides=overrides):
                row = {"review": review(**overrides)}
                self.assertIsNone(
                    module.accepted_review(
                        row,
                        target_commit=COMMIT,
                        target_tree=TREE,
                    )
                )

    def test_missing_or_invalid_review_timestamp_is_rejected(self) -> None:
        module = load_module()
        for timestamp in (None, "", "2026-09-02", "not-a-time"):
            with self.subTest(timestamp=timestamp):
                candidate = review()
                if timestamp is None:
                    candidate.pop("reviewed_at")
                else:
                    candidate["reviewed_at"] = timestamp
                self.assertIsNone(
                    module.accepted_review(
                        {"review": candidate},
                        target_commit=COMMIT,
                        target_tree=TREE,
                    )
                )

    def test_evidence_schema_requires_exact_independent_review_identity(self) -> None:
        module = load_module()
        schema = module.load_object(
            ROOT / "docs/evidence/schemas/trillionnium-evidence-v1.schema.json"
        )
        review_schema = schema["properties"]["review"]
        required = set(review_schema["required"])
        self.assertTrue(
            {
                "independent",
                "self_review",
                "reviewed_commit",
                "reviewed_tree",
                "reviewed_at",
                "reviewer_identity",
            }
            <= required
        )
        self.assertEqual(
            review_schema["properties"]["independent"]["const"],
            True,
        )
        self.assertEqual(
            review_schema["properties"]["self_review"]["const"],
            False,
        )
        self.assertEqual(
            schema["$defs"]["artifact"]["properties"]["size_bytes"]["minimum"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
