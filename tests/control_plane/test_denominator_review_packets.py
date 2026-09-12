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
        packet = {
            "schema": "trillionnium.denominator-family-review-packet.v1",
            "family_id": family,
            "source_manifest": f"manifests/{family}.json",
            "source_manifest_sha256": "1" * 64,
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
                "login": "claimed-reviewer",
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
                    "evidence_ids": ["FAKE-OR-UNRESOLVED-EVIDENCE-A"],
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
        output = directory / "proposal.json"
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

    def test_obvious_self_attestation_is_rejected_before_leaf_processing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["reviewer"]["login"] = "author"
            decision["leaves"] = []
            with self.assertRaisesRegex(self.accept.DecisionError, "self-attest"):
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

    def test_positive_classification_requires_reference_but_not_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["leaves"][0]["evidence_ids"] = []
            with self.assertRaisesRegex(
                self.accept.DecisionError, "evidence reference required"
            ):
                self.finalize_decision(packet_path, decision, directory)

    def test_valid_local_decision_is_only_a_non_authoritative_proposal(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            result, output = self.finalize_decision(
                packet_path, self.valid_decision(packet_path), directory
            )
            self.assertEqual(
                result["schema"],
                "trillionnium.denominator-family-decision-proposal.v1",
            )
            self.assertTrue(result["proposal_complete"])
            self.assertFalse(result["accepted"])
            self.assertFalse(result["authority"]["materialization_supported"])
            self.assertFalse(result["authority"]["reviewer_identity_verified"])
            self.assertFalse(result["authority"]["evidence_admission_verified"])
            self.assertEqual(result["packet_sha256"], digest(packet_path.read_bytes()))
            self.assertEqual(
                result["candidate_binding_sha256"], binding_digest(binding())
            )
            self.assertEqual(json.loads(output.read_text()), result)

    def test_untrusted_reviewer_and_receipt_fields_are_redacted_and_non_authoritative(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            packet_path = self.write_packet(directory)
            decision = self.valid_decision(packet_path)
            decision["reviewer"]["login"] = "same-person-alias-2"
            decision["reviewer"]["private_token"] = "do-not-copy-this-token"
            decision["reviewer"]["private_note"] = "do-not-copy-this-note"
            decision["authority_receipt"] = {
                "verified": True,
                "signature": "attacker-controlled-signature",
                "issued_at": "2026-09-11T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "nonce": "replayed",
            }
            decision["accepted"] = True
            result, output = self.finalize_decision(packet_path, decision, directory)
            self.assertFalse(result["accepted"])
            self.assertFalse(result["authority"]["expiry_and_nonce_verified"])
            self.assertFalse(result["authority"]["replay_protection_verified"])
            self.assertEqual(
                set(result["claimed_reviewer"]),
                {
                    "login",
                    "role",
                    "conflict_free_attestation",
                    "reviewed_binding_sha256",
                },
            )
            encoded = output.read_text(encoding="utf-8")
            for secret in (
                "do-not-copy-this-token",
                "do-not-copy-this-note",
                "attacker-controlled-signature",
                "replayed",
            ):
                self.assertNotIn(secret, encoded)

    def write_global_fixture(self, directory: Path):
        candidate = binding()
        candidate_digest = binding_digest(candidate)
        author = "author"
        families = []
        proposal_paths = []
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
            proposal = {
                "schema": "trillionnium.denominator-family-decision-proposal.v1",
                "family_id": family,
                "packet_sha256": packet_digest,
                "source_manifest": f"manifests/{family}.json",
                "source_manifest_sha256": source_digest,
                "candidate_author": author,
                "candidate_binding": candidate,
                "candidate_binding_sha256": candidate_digest,
                "claimed_reviewer": {
                    "login": "same-claimed-family-reviewer",
                    "role": "compatibility",
                    "conflict_free_attestation": True,
                    "reviewed_binding_sha256": candidate_digest,
                    "private_token": "family-secret-must-not-propagate",
                },
                "leaf_count": leaf_count,
                "blocker_count": 0,
                "requested_family_decision": "accept",
                "proposal_complete": True,
                "authority": {
                    "materialization_supported": False,
                    "accepted": False,
                },
                "accepted": False,
            }
            path = directory / f"{family}.proposal.json"
            path.write_bytes(canonical(proposal))
            proposal_paths.append(path)

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
                for path in proposal_paths
            ],
            "reviewer": {
                "login": "same-claimed-family-reviewer",
                "role": "global-compatibility",
                "conflict_free_attestation": True,
                "reviewed_binding_sha256": candidate_digest,
                "private_token": "global-secret-must-not-propagate",
            },
            "decision": "accept",
            "authority_receipt": {
                "verified": True,
                "signature": "global-attacker-controlled-signature",
                "nonce": "global-replayed",
            },
        }
        global_path = directory / "global.json"
        global_path.write_bytes(canonical(global_value))
        return index_path, proposal_paths, global_path

    def test_global_bundle_never_materializes_sg1_or_copies_untrusted_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index_path, proposals, global_path = self.write_global_fixture(directory)
            output = directory / "result.json"
            result = self.global_finalize.finalize(
                index_path, proposals, global_path, output
            )
            self.assertTrue(result["proposal_bundle_complete"])
            self.assertFalse(result["global_sg1_accepted"])
            self.assertFalse(result["authority"]["materialization_supported"])
            self.assertFalse(result["authority"]["principal_separation_verified"])
            self.assertFalse(result["authority"]["replay_protection_verified"])
            self.assertEqual(
                set(result["claimed_global_reviewer"]),
                {
                    "login",
                    "role",
                    "conflict_free_attestation",
                    "reviewed_binding_sha256",
                },
            )
            encoded = output.read_text(encoding="utf-8")
            for secret in (
                "family-secret-must-not-propagate",
                "global-secret-must-not-propagate",
                "global-attacker-controlled-signature",
                "global-replayed",
            ):
                self.assertNotIn(secret, encoded)

    def test_global_rejects_duplicate_drift_forged_acceptance_and_leaf_total(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            index_path, proposals, global_path = self.write_global_fixture(directory)
            output = directory / "result.json"

            duplicate = proposals[:-1] + [proposals[0]]
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "duplicate family"
            ):
                self.global_finalize.finalize(
                    index_path, duplicate, global_path, output
                )

            index_path, proposals, global_path = self.write_global_fixture(directory)
            first = json.loads(proposals[0].read_text())
            first["packet_sha256"] = "f" * 64
            proposals[0].write_bytes(canonical(first))
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "packet digest drift"
            ):
                self.global_finalize.finalize(
                    index_path, proposals, global_path, output
                )

            index_path, proposals, global_path = self.write_global_fixture(directory)
            first = json.loads(proposals[0].read_text())
            first["accepted"] = True
            first["authority"]["accepted"] = True
            proposals[0].write_bytes(canonical(first))
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "forged accepted flag"
            ):
                self.global_finalize.finalize(
                    index_path, proposals, global_path, output
                )

            index_path, proposals, global_path = self.write_global_fixture(directory)
            index_value = json.loads(index_path.read_text())
            index_value["families"][0]["leaf_count"] -= 1
            index_path.write_bytes(canonical(index_value))
            with self.assertRaisesRegex(
                self.global_finalize.GlobalDecisionError, "leaf total"
            ):
                self.global_finalize.finalize(
                    index_path, proposals, global_path, output
                )


if __name__ == "__main__":
    unittest.main()
