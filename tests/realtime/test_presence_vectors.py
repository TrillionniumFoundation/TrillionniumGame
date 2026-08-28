from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "contracts/realtime/presence-vectors.v1.json"


class ModelError(RuntimeError):
    pass


class Model:
    def __init__(self) -> None:
        self.connections: dict[str, dict[str, object]] = {}
        self.presences: set[tuple[str, str, str]] = set()
        self.epochs: dict[str, int] = {}
        self.closed = 0

    def execute(self, operation: str) -> None:
        parts = operation.split(":")
        op = parts[0]
        if op == "set-epoch":
            self.epochs[parts[1]] = int(parts[2][1:])
            return
        if op == "open":
            connection, session, user, node = parts[1:5]
            generation = int(parts[5][1:])
            epoch = int(parts[6][1:])
            if self.epochs.get(user, epoch) != epoch:
                raise ModelError("user_revocation_epoch_mismatch")
            self.epochs.setdefault(user, epoch)
            self.connections[connection] = {"session": session, "user": user, "node": node, "generation": generation, "draining": False, "queued": 0, "last_seen": 100}
            return
        if op == "rebind":
            record = self.route(parts[1], parts[2], int(parts[3][1:]))
            record["node"] = parts[4]
            record["generation"] = int(record["generation"]) + 1
            record["draining"] = False
            return
        if op == "route":
            self.route(parts[1], parts[2], int(parts[3][1:]))
            return
        if op == "drain":
            for record in self.connections.values():
                if record["node"] == parts[1]:
                    record["draining"] = True
            return
        if op == "join":
            record = self.route(parts[1], parts[2], int(parts[3][1:]))
            if record["draining"]:
                raise ModelError("connection_draining")
            self.presences.add((parts[4], str(record["user"]), str(record["session"])))
            return
        if op == "close":
            self.route(parts[1], parts[2], int(parts[3][1:]))
            self.remove(parts[1])
            return
        if op == "revoke":
            user = parts[1]
            expected, new = int(parts[2][1:]), int(parts[3][1:])
            if self.epochs.get(user, 0) != expected:
                raise ModelError("user_revocation_epoch_mismatch")
            self.epochs[user] = new
            for connection in [key for key, value in self.connections.items() if value["user"] == user]:
                self.remove(connection)
            return
        if op == "reserve":
            record = self.route(parts[1], parts[2], int(parts[3][1:]))
            value = int(parts[4])
            if int(record["queued"]) + value > 10:
                raise ModelError("slow_consumer_budget_exceeded")
            record["queued"] = int(record["queued"]) + value
            return
        if op == "expire":
            maximum = int(parts[3].removeprefix("max"))
            for connection in sorted(self.connections)[:maximum]:
                self.remove(connection)
            return
        raise AssertionError(operation)

    def route(self, connection: str, node: str, generation: int) -> dict[str, object]:
        record = self.connections[connection]
        if record["node"] != node:
            raise ModelError("route_owner_mismatch")
        if record["generation"] != generation:
            raise ModelError("route_generation_mismatch")
        return record

    def remove(self, connection: str) -> None:
        record = self.connections.pop(connection)
        self.presences = {key for key in self.presences if not (key[1] == record["user"] and key[2] == record["session"])}
        self.closed += 1


class PresenceVectorTests(unittest.TestCase):
    def test_vectors(self) -> None:
        document = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "trillionnium.presence-vectors.v1")
        self.assertGreaterEqual(len(document["cases"]), 8)
        self.assertFalse(any(document["claims"].values()))
        for case in document["cases"]:
            model = Model()
            captured = None
            try:
                for operation in case["operations"]:
                    model.execute(operation)
            except ModelError as error:
                captured = str(error)
            if "expect_error" in case:
                self.assertEqual(captured, case["expect_error"], case["id"])
                continue
            self.assertIsNone(captured, case["id"])
            expected = case["expect"]
            if "connections" in expected:
                self.assertEqual(len(model.connections), expected["connections"], case["id"])
            if "presences" in expected:
                self.assertEqual(len(model.presences), expected["presences"], case["id"])
            if "closed" in expected:
                self.assertEqual(model.closed, expected["closed"], case["id"])


if __name__ == "__main__":
    unittest.main()
