from __future__ import annotations
import unittest
from pathlib import Path
from tools.oracle.differential import compare_corpus, compare_pair
from tools.oracle.normalize import load_registry

ROOT = Path(__file__).resolve().parents[2]


def observation(lane: str = "immutable", attempt: int = 1):
    return {
        "schema": "trillionnium.oracle-observation.v1",
        "lane": lane,
        "run_id": f"{lane}-{attempt}",
        "case_id": "account-get",
        "attempt": attempt,
        "input_sha256": "sha256:" + "a" * 64,
        "surfaces": {
            "account": {
                "surface": "account",
                "value": {
                    "user": {
                        "id": "u1",
                        "username": "name",
                        "create_time": "clock-a",
                        "update_time": "clock-a",
                    }
                },
            },
            "http": {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_class": "json",
            },
            "database_effects": [],
            "hooks": [],
            "provider_intents": [],
            "metrics": {"requests": 1},
        },
    }


class DifferentialTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry(ROOT / "config/oracle-normalizers.json")

    def test_allowed_clock_fields_normalize(self):
        immutable = observation()
        instrumented = observation("instrumented")
        instrumented["surfaces"]["account"]["value"]["user"]["create_time"] = "clock-b"
        instrumented["surfaces"]["account"]["value"]["user"]["update_time"] = "clock-b"
        self.assertEqual(compare_pair(immutable, instrumented, self.registry), [])

    def test_http_status_divergence_is_p1(self):
        immutable = observation()
        instrumented = observation("instrumented")
        instrumented["surfaces"]["http"]["status"] = 500
        divergences = compare_pair(immutable, instrumented, self.registry)
        self.assertEqual(divergences[0].severity, "P1")

    def test_database_effect_divergence_is_p0(self):
        immutable = observation()
        instrumented = observation("instrumented")
        instrumented["surfaces"]["database_effects"] = [
            {"table": "users", "operation": "insert"}
        ]
        divergences = compare_pair(immutable, instrumented, self.registry)
        self.assertEqual(divergences[0].severity, "P0")

    def test_pair_identity_mismatch_is_rejected(self):
        immutable = observation()
        instrumented = observation("instrumented")
        instrumented["case_id"] = "other"
        with self.assertRaises(ValueError):
            compare_pair(immutable, instrumented, self.registry)

    def test_raw_token_is_rejected(self):
        immutable = observation()
        immutable["surfaces"]["session"] = {"token": "secret"}
        with self.assertRaises(ValueError):
            compare_pair(immutable, observation("instrumented"), self.registry)

    def test_ten_attempt_stable_corpus_still_grants_no_claim(self):
        corpus = []
        for attempt in range(1, 11):
            corpus.extend(
                [observation(attempt=attempt), observation("instrumented", attempt)]
            )
        value = compare_corpus(corpus, self.registry)
        self.assertEqual(
            value["divergence_counts"], {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        )
        self.assertFalse(any(value["claims"].values()))

    def test_missing_attempt_is_rejected(self):
        corpus = []
        for attempt in range(1, 10):
            corpus.extend(
                [observation(attempt=attempt), observation("instrumented", attempt)]
            )
        with self.assertRaises(ValueError):
            compare_corpus(corpus, self.registry)

    def test_lane_nondeterminism_is_p1(self):
        corpus = []
        for attempt in range(1, 11):
            immutable = observation(attempt=attempt)
            instrumented = observation("instrumented", attempt)
            if attempt == 10:
                immutable["surfaces"]["http"]["headers"]["x-variant"] = "yes"
            corpus.extend([immutable, instrumented])
        value = compare_corpus(corpus, self.registry)
        self.assertGreater(value["divergence_counts"]["P1"], 0)


if __name__ == "__main__":
    unittest.main()
