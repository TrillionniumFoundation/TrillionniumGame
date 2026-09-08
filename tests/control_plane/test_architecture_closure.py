from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/roadmap/ARCHITECTURE_CLOSURE.json"
GAPS = ROOT / "docs/status/GAP_REGISTER.json"
AUTH_SOURCE = ROOT / "crates/trnm-persistence-pg/src/auth.rs"


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


class ArchitectureClosureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_object(PLAN)
        cls.gap_register = load_object(GAPS)
        cls.known_gaps = {
            row["id"]
            for row in cls.gap_register["gaps"]
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }

    def test_identity_and_non_negotiable_scope(self) -> None:
        self.assertEqual(self.plan["schema"], "trillionnium.architecture-closure.v1")
        self.assertEqual(self.plan["project_id"], "trillionnium-game")
        self.assertEqual(self.plan["plan_version"], 3)
        baseline = self.plan["source_baseline"]
        self.assertRegex(baseline["base_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(baseline["base_tree"], r"^[0-9a-f]{40}$")
        principles = self.plan["non_negotiable_principles"]
        self.assertGreaterEqual(len(principles), 8)
        joined = "\n".join(principles).lower()
        self.assertIn("complete nakama oss d0-d8 denominator", joined)
        self.assertIn("one first-party trnm-server composition root", joined)
        self.assertIn("private sha", joined)
        self.assertIn("cannot be synthesized", joined)

    def test_delivery_tracks_do_not_conflate_core_with_full_parity(self) -> None:
        tracks = {row["id"]: row for row in self.plan["delivery_tracks"]}
        self.assertEqual(set(tracks), {"TRACK-CORE-DELIVERY", "TRACK-NAKAMA-PARITY"})
        core = tracks["TRACK-CORE-DELIVERY"]
        parity = tracks["TRACK-NAKAMA-PARITY"]
        self.assertEqual(core["claim_ceiling_before_independent_acceptance"], "source-candidate")
        self.assertEqual(parity["claim_ceiling_before_global_sg1_acceptance"], "C0-planning")
        self.assertEqual(parity["family_count"], 14)
        self.assertGreater(parity["candidate_leaf_count"], 10_000)

    def test_phases_are_ordered_and_reference_only_registered_gaps(self) -> None:
        phases = self.plan["phases"]
        self.assertEqual([row["priority"] for row in phases], list(range(len(phases))))
        self.assertEqual(len({row["id"] for row in phases}), len(phases))
        for phase in phases:
            with self.subTest(phase=phase["id"]):
                self.assertTrue(phase["work"])
                self.assertTrue(phase["exit_criteria"])
                self.assertTrue(phase["gap_links"])
                self.assertLessEqual(set(phase["gap_links"]), self.known_gaps)
        security = next(row for row in phases if row["id"] == "AC-1")
        self.assertIn("GAP-P0-CRYPTO-001", security["gap_links"])
        self.assertIn("GAP-P1-CRYPTO-002", security["gap_links"])

    def test_active_server_auth_uses_reviewed_crypto_boundary(self) -> None:
        production = AUTH_SOURCE.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        self.assertIn("PKey::hmac", production)
        self.assertIn("Signer::new(MessageDigest::sha256()", production)
        self.assertIn("memcmp::eq", production)
        self.assertIn("hash(MessageDigest::sha256()", production)
        self.assertNotIn("KeyRing", production)
        self.assertNotIn("sha256_digest", production)
        self.assertNotIn("constant_time_eq", production)

    def test_external_facts_cannot_be_auto_closed(self) -> None:
        blockers = self.plan["external_blockers"]
        self.assertGreaterEqual(len(blockers), 3)
        for blocker in blockers:
            with self.subTest(blocker=blocker["id"]):
                self.assertFalse(blocker["may_auto_close"])
                self.assertTrue(blocker["actor"].strip())
                self.assertTrue(blocker["required_fact"].strip())
        actors = "\n".join(row["actor"] for row in blockers).lower()
        self.assertIn("administrator", actors)
        self.assertIn("human", actors)
        self.assertIn("independent", actors)

    def test_claim_boundary_remains_fail_closed(self) -> None:
        claims = self.plan["claim_boundary"]
        expected_false = {
            "all_gaps_closed",
            "accepted_evidence",
            "complete_nakama_compatibility",
            "production_ready",
            "public_online",
            "cutover_authorized",
            "nakama_retired",
            "administrator_bypass",
        }
        self.assertEqual(set(claims), expected_false)
        self.assertTrue(all(claims[name] is False for name in expected_false))


if __name__ == "__main__":
    unittest.main()
