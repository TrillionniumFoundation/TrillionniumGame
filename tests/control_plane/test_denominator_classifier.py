from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/classify-denominator-candidates.py"
RULES = ROOT / "docs/development/DENOMINATOR_CLASSIFICATION_RULES.json"


class DenominatorClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("classify_denominator_candidates", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.rules = json.loads(RULES.read_text(encoding="utf-8"))

    def test_every_known_denominator_has_exactly_one_catch_all_rule(self) -> None:
        rows = self.module.validate_rules(self.rules)
        counts: dict[str, int] = {}
        for row in rows:
            self.assertEqual(row["match"], {"any": True})
            for denominator in row["denominator_ids"]:
                counts[denominator] = counts.get(denominator, 0) + 1
        self.assertEqual(set(counts.values()), {1})
        self.assertEqual(len(counts), 14)

    def test_api_candidate_is_classified_but_receives_no_credit(self) -> None:
        candidate = {
            "schema": "synthetic.candidate.v1",
            "denominator_id": "DEN-API",
            "leaves": [
                {"leaf_id": "api:method:AuthenticateDevice", "kind": "method"},
                {"leaf_id": "api:route:POST:/v2/account/authenticate/device", "kind": "route"},
            ],
        }
        result = self.module.classify(candidate, self.rules, None)
        self.assertEqual(result["leaf_count"], 2)
        self.assertEqual(result["unclassified_count"], 0)
        self.assertEqual(result["ambiguous_count"], 0)
        self.assertFalse(result["claims"]["lock_accepted"])
        self.assertFalse(result["claims"]["compatibility_credit"])
        for leaf in result["leaves"]:
            self.assertEqual(leaf["owner_role"], "protocol")
            self.assertEqual(leaf["task_id"], "TG-W2-001")
            self.assertEqual(leaf["review_status"], "pending")
            self.assertFalse(leaf["compatibility_credit"])

    def test_empty_candidate_fails(self) -> None:
        with self.assertRaises(self.module.ClassificationError):
            self.module.classify(
                {"denominator_id": "DEN-RTAPI", "leaves": []},
                self.rules,
                None,
            )

    def test_duplicate_leaf_id_fails(self) -> None:
        with self.assertRaises(self.module.ClassificationError):
            self.module.classify(
                {
                    "denominator_id": "DEN-CONFIG",
                    "leaves": [
                        {"leaf_id": "config:key:socket.port"},
                        {"leaf_id": "config:key:socket.port"},
                    ],
                },
                self.rules,
                None,
            )

    def test_missing_rule_and_ambiguous_rule_fail(self) -> None:
        missing_rules = json.loads(json.dumps(self.rules))
        missing_rules["rules"] = [
            row for row in missing_rules["rules"] if "DEN-IAP" not in row["denominator_ids"]
        ]
        with self.assertRaises(self.module.ClassificationError):
            self.module.validate_rules(missing_rules)

        ambiguous_rules = json.loads(json.dumps(self.rules))
        duplicate = json.loads(json.dumps(ambiguous_rules["rules"][1]))
        duplicate["id"] = "CLASS-API-DUPLICATE"
        ambiguous_rules["rules"].append(duplicate)
        with self.assertRaises(self.module.ClassificationError):
            self.module.classify(
                {"denominator_id": "DEN-API", "leaves": [{"leaf_id": "api:x"}]},
                ambiguous_rules,
                None,
            )

    def test_cli_writes_deterministic_output(self) -> None:
        candidate = {
            "denominator_id": "DEN-SDK",
            "leaves": [{"leaf_id": "sdk:javascript:authenticateDevice"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lock.json"
            first = self.module.classify(candidate, self.rules, None)
            output.write_text(json.dumps(first, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            second = self.module.classify(candidate, self.rules, None)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), first)


if __name__ == "__main__":
    unittest.main()
