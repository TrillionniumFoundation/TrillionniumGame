from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "contracts/protocol/transport-error-vectors.v1.json"

HTTP = {
    "invalid_argument": 400,
    "not_found": 404,
    "already_exists": 409,
    "permission_denied": 403,
    "resource_exhausted": 429,
    "failed_precondition": 412,
    "aborted": 409,
    "out_of_range": 400,
    "unimplemented": 501,
    "internal": 500,
    "unavailable": 503,
    "data_loss": 500,
    "unauthenticated": 401,
}
GRPC = {
    "invalid_argument": 3,
    "not_found": 5,
    "already_exists": 6,
    "permission_denied": 7,
    "resource_exhausted": 8,
    "failed_precondition": 9,
    "aborted": 10,
    "out_of_range": 11,
    "unimplemented": 12,
    "internal": 13,
    "unavailable": 14,
    "data_loss": 15,
    "unauthenticated": 16,
}


class TransportVectorTests(unittest.TestCase):
    def test_stable_table(self) -> None:
        document = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "trillionnium.transport-error-vectors.v1")
        self.assertFalse(any(document["claims"].values()))
        self.assertEqual(len(document["stable"]), len(GRPC))
        for item in document["stable"]:
            self.assertEqual(item["grpc"], GRPC[item["code"]])
            self.assertEqual(item["http"], HTTP[item["code"]])
            self.assertEqual(item["rt"], 0 if item["code"] in {"internal", "unavailable", "data_loss"} else 3)

    def test_context_codes_are_complete_and_unique(self) -> None:
        document = json.loads(VECTORS.read_text(encoding="utf-8"))
        expected = {
            "unrecognized_payload": 1,
            "missing_payload": 2,
            "match_not_found": 4,
            "match_join_rejected": 5,
            "runtime_function_not_found": 6,
            "runtime_function_exception": 7,
        }
        observed = {item["context"]: item["rt"] for item in document["contexts"]}
        self.assertEqual(observed, expected)
        self.assertEqual(len(set(observed.values())), len(expected))


if __name__ == "__main__":
    unittest.main()
