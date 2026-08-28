from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "database/schema/v2/database-profile-contract.v2.json"
VERIFIER = ROOT / "scripts/verify-database-profile-v2.py"


class DatabaseProfileV2ContractTest(unittest.TestCase):
    def run_verifier(self) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(VERIFIER)],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        return json.loads(completed.stdout)

    def test_static_contract_passes_without_runtime_overclaim(self) -> None:
        report = self.run_verifier()
        self.assertEqual(
            report["schema"],
            "trillionnium.game.database-profile-verification.v2",
        )
        self.assertTrue(report["claims"]["static_contract_passed"])
        self.assertFalse(report["claims"]["runtime_apply_passed"])
        self.assertFalse(report["claims"]["fault_matrix_complete"])
        self.assertFalse(report["claims"]["production_ready"])

    def test_profiles_have_distinct_lease_strategies(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        profiles = contract["profiles"]
        self.assertEqual(set(profiles), {"postgresql", "cockroachdb"})
        self.assertEqual(
            profiles["postgresql"]["lease_strategy"],
            "select_for_update_skip_locked",
        )
        self.assertEqual(
            profiles["cockroachdb"]["lease_strategy"],
            "optimistic_compare_and_swap_with_transaction_retry",
        )
        self.assertNotEqual(
            profiles["postgresql"]["lease_strategy"],
            profiles["cockroachdb"]["lease_strategy"],
        )

    def test_retry_classification_is_fail_closed(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["profiles"]["postgresql"]["retry_sqlstates"],
            ["40001", "40P01"],
        )
        self.assertEqual(
            contract["profiles"]["cockroachdb"]["retry_sqlstates"],
            ["40001"],
        )
        for profile in contract["profiles"].values():
            self.assertNotIn("23505", profile["retry_sqlstates"])

    def test_all_business_identifiers_are_application_supplied(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        invariants = set(contract["required_invariants"])
        forbidden = set(contract["forbidden_schema_features"])
        self.assertIn("all_identifiers_are_application_supplied", invariants)
        self.assertIn("database_generated_command_id", forbidden)
        self.assertIn("database_generated_event_id", forbidden)
        self.assertIn("database_generated_intent_id", forbidden)


if __name__ == "__main__":
    unittest.main()
