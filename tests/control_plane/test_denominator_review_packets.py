from __future__ import annotations

import hashlib
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
GLOBAL = ROOT / "scripts/finalize-global-sg1.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def canonical(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def binding() -> dict[str, str]:
    return {
        "repository": "TrillionniumFoundation/TrillionniumGame",
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "prospective_merge": "c" * 40,
        "prospective_merge_tree": "d" * 40,
    }


def binding_digest(value: dict[str, str]) -> str:
    return digest(canonical(value))


class DenominatorReviewPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = load(GENERATOR, "denominator_generator_tests")
        cls.checker = load(CHECKER, "denominator_checker_tests")
        cls.accept = load(ACCEPT, "denominator_accept_tests")
        cls.global_finalize = load(GLOBAL, "denominator_global_tests")

    def test_tracked_packets_are_reproducible_and_complete(self):
        index = self.checker.validate()
        self.assertEqual(index["family_count"], 14)
        self.assertEqual(index["leaf_count"], 10173)

    def write_packet(self, directory: Path, family: str = "test-family") -> Path:
        source_digest = "1" * 64
        packet = {
            "schema": "trillionnium.denominator-family-review-packet.v1",
            "family_id": family,
            "source_manifest": f"manifests/{family}.json",
            "source_manifest_sha256": source_digest,
            "leaf_count": 2,
            "leaves": [
                {"leaf_id": "leaf-a", "source_leaf_sha256": "2" * 64},
                {"leaf_id": "leaf-b", "source_leaf_sha256": "3" * 64},
            ],
        }
        path = directory / f"{family}.packet.json"
        path.write_bytes(canonical(packet))
        return path

    def valid_decision(self, packet_path: Path) -> dict:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        candidate = binding()
        return {
            "schema": "trillionnium.denominator-family-human-decision.v1",
            "family_id": packet["family_id"],
            "packet_sha256": digest(packet_path.read_bytes()),
            "source_manifest": packet["source_manifest"],
            "source_manifest_sha256": packet["source_manifest_sha256"],
            "candidate_author": "author",
            "candidate_binding": candidate,
            "reviewer": {
                "login": "reviewer",
                "role": "compatibility",
                "conflict_free_attestation": True,
                "reviewed_binding_sha256": binding_digest(candidate),
            },
            "family_decision": "accept",
            "leaves": [
                {
                    "leaf_id": "leaf-a",
                    "source_leaf_sha256": "2" * 64,
                    "classification": "implemented-compatible",
                    "rationale": "wire and durable effects matched the exact oracle",
                    "evidence_ids": ["EVIDENCE-A"],
                },
                {
                    "leaf_id": "leaf-b",
                    "source_leaf_sha256": "3" * 64,
                    "classification": "not-applicable-with-rationale",
                    "rationale": "the pinned profile excludes this optional extension",
                    "evidence_ids": [],
                },
            ],
        }

    def finalize_decision(self, packet_path: Path, decision: dict, directory: Path):
        source = directory / "decision.json"
        output = directory / "accepted.json"
        source.write_text(json.dumps(decision), encoding="utf-8")
        return self.accept.finalize(packet_path, source, output), output

    def test_missing_leaf_decision_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["leaves"] = []
            with self.assertRaisesRegex(self.accept.DecisionError, "leaf denominator"):
                self.finalize_decision(packet_path, decision, directory)

    def test_self_approval_is_rejected_before_leaf_processing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["reviewer"]["login"] = "author"
            decision["leaves"] = []
            with self.assertRaisesRegex(self.accept.DecisionError, "self-approve"):
                self.finalize_decision(packet_path, decision, directory)

    def test_packet_manifest_and_exact_tuple_substitution_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["packet_sha256"] = "0" * 64
            with self.assertRaisesRegex(self.accept.DecisionError, "packet digest"):
                self.finalize_decision(packet_path, decision, directory)

            decision = self.valid_decision(packet_path)
            decision["source_manifest_sha256"] = "0" * 64
            with self.assertRaisesRegex(self.accept.DecisionError, "manifest digest"):
                self.finalize_decision(packet_path, decision, directory)

            decision = self.valid_decision(packet_path)
            decision["candidate_binding"]["source_head"] = "short"
            with self.assertRaisesRegex(self.accept.DecisionError, "source_head"):
                self.finalize_decision(packet_path, decision, directory)

            decision = self.valid_decision(packet_path)
            decision["reviewer"]["reviewed_binding_sha256"] = "0" * 64
            with self.assertRaisesRegex(self.accept.DecisionError, "binding digest"):
                self.finalize_decision(packet_path, decision, directory)

    def test_positive_compatibility_classification_requires_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["leaves"][0]["evidence_ids"] = []
            with self.assertRaisesRegex(self.accept.DecisionError, "evidence required"):
                self.finalize_decision(packet_path, decision, directory)

    def test_valid_family_decision_is_exactly_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            result, output = self.finalize_decision(packet_path, decision, directory)
            self.assertTrue(result["accepted"])
            self.assertEqual(result["packet_sha256"], digest(packet_path.read_bytes()))
            self.assertEqual(
                result["candidate_binding_sha256"], binding_digest(binding())
            )
            self.assertEqual(json.loads(output.read_text()), result)

    def write_global_fixture(self, directory: Path):
        candidate = binding()
        candidate_digest = binding_digest(candidate)
        author = "author"
        families = []
        decision_paths = []
        for index in range(14):
            family = f"family-{index:02d}"
            packet_digest = f"{index + 1:064x}"
            source_digest = f"{index + 101:064x}"
            leaf_count = 10160 if index == 0 else 1
            families.append(
                {
                    "family_id": family,
                    "packet": f"{family}.candidate.json",
                    "packet_sha256": packet_digest,
                    "source_manifest": f"manifests/{family}.json",
                    "source_manifest_sha256": source_digest,
                    "leaf_count": leaf_count,
                }
            )
            decision = {
                "schema": "trillionnium.denominator-family-decision.v1",
                "family_id": family,
                "packet_sha256": packet_digest,
                "source_manifest": f"manifests/{family}.json",
                "source_manifest_sha256": source_digest,
                "candidate_author": author,
                "candidate_binding": candidate,
                "candidate_binding_sha256": candidate_digest,
                "reviewer": {
                    "login": "family-reviewer",
                    "role": "compatibility",
                    "conflict_free_attestation": True,
                    "reviewed_binding_sha256": candidate_digest,
                },
                "leaf_count": leaf_count,
                "blocker_count": 0,
                "family_decision": "accept",
                "accepted": True,
            }
            path = directory / f"{family}.decision.json"
            path.write_bytes(canonical(decision))
            decision_paths.append(path)

        index_value = {
            "schema": "trillionnium.denominator-family-review-index.v1",
            "family_count": 14,
            "leaf_count": 10173,
            "families": families,
        }
        index_path = directory / "index.json"
        index_path.write_bytes(canonical(index_value))
        global_value = {
            "schema": "trillionnium.global-sg1-human-decision.v1",
            "index_sha256": digest(index_path.read_bytes()),
            "candidate_author": author,
            "candidate_binding": candidate,
            "candidate_binding_sha256": candidate_digest,
            "family_decisions": [
                {
                    "family_id": json.loads(path.read_text())["family_id"],
                    "decision_sha256": digest(path.read_bytes()),
                }
                for path in decision_paths
            ],
            "reviewer": {
                "login": "global-reviewer",
                "role": "global-compatibility",
                "conflict_free_attestation": True,
                "reviewed_binding_sha256": candidate_digest,
            },
            "decision": "accept",
        }
        global_path = directory / "global.json"
        global_path.write_bytes(canonical(global_value))
        return index_path, decision_paths, global_path

    def test_global_sg1_rejects_digest_substitution_duplicates_and_reviewer_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index_path, decisions, global_path = self.write_global_fixture(directory)
            output = directory / "result.json"
            result = self.global_finalize.finalize(
                index_path, decisions, global_path, output
            )
            self.assertTrue(result["global_sg1_accepted"])

            duplicate = decisions[:-1] + [decisions[0]]
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "duplicate family"
            ):
                self.global_finalize.finalize(
                    index_path, duplicate, global_path, output
                )

            first = json.loads(decisions[0].read_text())
            first["packet_sha256"] = "f" * 64
            decisions[0].write_bytes(canonical(first))
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "packet digest drift"
            ):
                self.global_finalize.finalize(
                    index_path, decisions, global_path, output
                )

            index_path, decisions, global_path = self.write_global_fixture(directory)
            changed_global = json.loads(global_path.read_text())
            changed_global["reviewer"]["login"] = "family-reviewer"
            global_path.write_bytes(canonical(changed_global))
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "must be distinct"
            ):
                self.global_finalize.finalize(
                    index_path, decisions, global_path, output
                )


if __name__ == "__main__":
    unittest.main()
