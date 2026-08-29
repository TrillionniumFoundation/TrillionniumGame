from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "contracts/persistence/persistence-vectors.v1.json"


class ModelError(RuntimeError):
    pass


class Model:
    def __init__(self) -> None:
        self.revision = None
        self.generation = None
        self.last_event_sequence = 0
        self.commands: dict[str, tuple[str, int]] = {}
        self.outbox: dict[str, dict[str, object]] = {}
        self.last_duplicate_revision = None

    def execute(self, operation: dict[str, object]) -> None:
        op = operation["op"]
        if op == "bootstrap":
            if self.revision is not None:
                raise ModelError("entity_already_exists")
            self.revision = 0
            self.generation = int(operation["generation"])
            return
        if self.revision is None:
            raise ModelError("entity_not_found")
        if op == "takeover":
            if self.generation != int(operation["expected_generation"]):
                raise ModelError("authority_generation_mismatch")
            self.generation += 1
            return
        if op == "duplicate":
            command = str(operation["command"])
            fingerprint = str(operation["fingerprint"])
            stored = self.commands.get(command)
            if stored is None:
                raise ModelError("command_not_found")
            if stored[0] != fingerprint:
                raise ModelError("command_id_conflict")
            self.last_duplicate_revision = stored[1]
            return
        if op == "commit":
            command = str(operation["command"])
            fingerprint = str(operation["fingerprint"])
            if command in self.commands:
                if self.commands[command][0] == fingerprint:
                    self.last_duplicate_revision = self.commands[command][1]
                    return
                raise ModelError("command_id_conflict")
            if self.generation != int(operation["generation"]):
                raise ModelError("authority_generation_mismatch")
            if self.revision != int(operation["expected_revision"]):
                raise ModelError("entity_revision_mismatch")
            new_outbox = [str(item) for item in operation.get("outbox", [])]
            if any(item in self.outbox for item in new_outbox):
                raise ModelError("outbox_intent_already_exists")
            self.revision += 1
            self.last_event_sequence += len(operation.get("events", []))
            self.commands[command] = (fingerprint, self.revision)
            for intent in new_outbox:
                self.outbox[intent] = {
                    "state": "pending",
                    "lease_generation": 0,
                    "attempt": 0,
                }
            return
        if op == "lease":
            record = self.outbox[str(operation["intent"])]
            if record["state"] != "pending":
                raise ModelError("outbox_not_pending")
            record["lease_generation"] = int(record["lease_generation"]) + 1
            record["state"] = f"leased:{operation['owner']}"
            return
        if op == "retry":
            record = self.outbox[str(operation["intent"])]
            expected = f"leased:{operation['owner']}"
            if record["state"] != expected or record["lease_generation"] != int(operation["lease_generation"]):
                raise ModelError("outbox_lease_mismatch")
            record["attempt"] = int(record["attempt"]) + 1
            record["state"] = "pending"
            return
        if op == "apply":
            record = self.outbox[str(operation["intent"])]
            expected = f"leased:{operation['owner']}"
            if record["state"] != expected or record["lease_generation"] != int(operation["lease_generation"]):
                raise ModelError("outbox_lease_mismatch")
            record["state"] = f"applied:{operation['receipt']}"
            return
        raise AssertionError(f"unknown operation {op}")


class PersistenceVectorTests(unittest.TestCase):
    def test_vectors(self) -> None:
        document = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "trillionnium.persistence-vectors.v1")
        self.assertGreaterEqual(len(document["cases"]), 6)
        self.assertFalse(any(document["claims"].values()))
        for case in document["cases"]:
            model = Model()
            captured_error = None
            try:
                for operation in case["operations"]:
                    model.execute(operation)
            except ModelError as error:
                captured_error = str(error)
            if "expect_error" in case:
                self.assertEqual(captured_error, case["expect_error"], case["id"])
                continue
            self.assertIsNone(captured_error, case["id"])
            expected = case["expect"]
            if "revision" in expected:
                self.assertEqual(model.revision, expected["revision"], case["id"])
            if "last_event_sequence" in expected:
                self.assertEqual(model.last_event_sequence, expected["last_event_sequence"], case["id"])
            if "event_count" in expected:
                self.assertEqual(model.last_event_sequence, expected["event_count"], case["id"])
            if "duplicate_revision" in expected:
                self.assertEqual(model.last_duplicate_revision, expected["duplicate_revision"], case["id"])
            for intent in expected.get("outbox_pending", []):
                self.assertEqual(model.outbox[intent]["state"], "pending", case["id"])


if __name__ == "__main__":
    unittest.main()
