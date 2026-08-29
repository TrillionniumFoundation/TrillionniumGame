from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRANSACTION_CONTRACT = (
    ROOT / "database/schema/v2/command-transaction-contract.v2.json"
)
VERIFIER = ROOT / "scripts/verify-command-transaction-v2.py"


class CommandTransactionV2ContractTest(unittest.TestCase):
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

    def test_phase_order_places_acknowledgement_after_commit(self) -> None:
        report = self.run_verifier()
        self.assertEqual(
            report["phases"],
            [
                "canonicalize",
                "begin",
                "idempotency_lookup",
                "authority_fence",
                "persist_graph",
                "commit",
                "acknowledge",
            ],
        )
        self.assertTrue(report["checks"]["ack_after_commit"])

    def test_exact_identity_is_reused_across_transaction_retry(self) -> None:
        contract = json.loads(TRANSACTION_CONTRACT.read_text(encoding="utf-8"))
        reused = set(contract["identity"]["retry_reuses"])
        self.assertGreaterEqual(
            reused,
            {
                "tenant_id",
                "entity_id",
                "command_id",
                "fingerprint",
                "event_ids",
                "outbox_intent_ids",
                "canonical_payload_bytes",
            },
        )
        self.assertTrue(contract["retry_policy"]["same_canonical_inputs"])
        self.assertEqual(contract["retry_policy"]["scope"], "entire_transaction")

    def test_unknown_commit_result_recovers_through_exact_duplicate(self) -> None:
        contract = json.loads(TRANSACTION_CONTRACT.read_text(encoding="utf-8"))
        acknowledge = next(
            phase
            for phase in contract["ordered_phases"]
            if phase["phase"] == "acknowledge"
        )
        requirements = set(acknowledge["requirements"])
        self.assertIn("ack_only_after_observed_commit_success", requirements)
        self.assertIn(
            "unknown_commit_result_retries_as_exact_duplicate",
            requirements,
        )
        self.assertIn("exact_duplicate_returns_stored_receipt_bytes", requirements)

    def test_non_retryable_conflicts_are_fail_closed(self) -> None:
        contract = json.loads(TRANSACTION_CONTRACT.read_text(encoding="utf-8"))
        forbidden_retry = set(contract["retry_policy"]["never_blind_retry"])
        self.assertGreaterEqual(
            forbidden_retry,
            {
                "idempotency_fingerprint_conflict",
                "authority_generation_conflict",
                "revision_conflict",
                "constraint_violation",
                "canonicalization_failure",
            },
        )
        for profile in ("postgresql", "cockroachdb"):
            classification = set(
                contract["retry_policy"][profile]["classification_required"]
            )
            self.assertGreaterEqual(classification, {"23505", "08006", "08007"})

    def test_static_verification_does_not_overclaim_runtime_maturity(self) -> None:
        report = self.run_verifier()
        self.assertTrue(report["claims"]["static_contract_passed"])
        self.assertFalse(report["claims"]["runtime_fault_matrix_complete"])
        self.assertFalse(report["claims"]["production_ready"])


if __name__ == "__main__":
    unittest.main()
