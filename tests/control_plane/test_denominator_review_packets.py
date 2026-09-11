from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts/generate-denominator-review-packets.py"
CHECKER = ROOT / "scripts/check-denominator-review-packets.py"
ACCEPT = ROOT / "scripts/accept-denominator-family.py"

def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

class DenominatorReviewPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = load(GENERATOR, "denominator_generator_tests")
        cls.checker = load(CHECKER, "denominator_checker_tests")
        cls.accept = load(ACCEPT, "denominator_accept_tests")

    def test_tracked_packets_are_reproducible_and_complete(self):
        index = self.checker.validate()
        self.assertEqual(index["family_count"], 14)
        self.assertEqual(index["leaf_count"], 10173)

    def test_missing_leaf_decision_is_rejected(self):
        index = json.loads((ROOT / "docs/review/denominator-family-packets/index.json").read_text())
        packet_path = ROOT / "docs/review/denominator-family-packets" / index["families"][0]["packet"]
        packet = json.loads(packet_path.read_text())
        decision = {
            "schema": "trillionnium.denominator-family-human-decision.v1",
            "family_id": packet["family_id"],
            "candidate_author": "author",
            "candidate_binding": {"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a","source_tree":"b","prospective_merge":"c","prospective_merge_tree":"d"},
            "reviewer": {"login":"reviewer","role":"compatibility","conflict_free_attestation":True},
            "family_decision":"accept",
            "leaves": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "decision.json"
            output = Path(temporary) / "accepted.json"
            source.write_text(json.dumps(decision))
            with self.assertRaisesRegex(self.accept.DecisionError, "leaf denominator"):
                self.accept.finalize(packet_path, source, output)

    def test_self_approval_is_rejected_before_leaf_processing(self):
        index = json.loads((ROOT / "docs/review/denominator-family-packets/index.json").read_text())
        packet_path = ROOT / "docs/review/denominator-family-packets" / index["families"][0]["packet"]
        packet = json.loads(packet_path.read_text())
        decision = {
            "schema":"trillionnium.denominator-family-human-decision.v1",
            "family_id":packet["family_id"],
            "candidate_author":"same",
            "candidate_binding":{"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a","source_tree":"b","prospective_merge":"c","prospective_merge_tree":"d"},
            "reviewer":{"login":"same","role":"compatibility","conflict_free_attestation":True},
            "family_decision":"reject",
            "leaves":[],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary)/"decision.json"; output=Path(temporary)/"accepted.json"; source.write_text(json.dumps(decision))
            with self.assertRaisesRegex(self.accept.DecisionError,"self-approve"):
                self.accept.finalize(packet_path,source,output)

if __name__ == "__main__":
    unittest.main()
