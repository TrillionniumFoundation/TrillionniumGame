from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class AuthorityModel:
    version: int = 0
    generation: int = 1
    sequences: dict[str, int] = field(default_factory=dict)
    receipts: dict[str, dict] = field(default_factory=dict)
    pending: dict[str, dict] = field(default_factory=dict)

    def prepare(self, step: dict) -> str:
        command = step["command"]
        receipt = self.receipts.get(command)
        if receipt is not None:
            exact = all(
                receipt[key] == step[key]
                for key in ("participant", "sequence", "version", "fingerprint")
            )
            if exact:
                return "replay"
            raise ValueError("command_id_conflict")
        if step["generation"] != self.generation:
            raise ValueError("stale_authority_generation")
        if step["version"] != self.version:
            raise ValueError("stale_match_version")
        if step["sequence"] != self.sequences.get(step["participant"], 0) + 1:
            raise ValueError("participant_sequence_mismatch")
        return "ready"

    def commit(self, step: dict, result: str) -> None:
        if step["generation"] != self.generation:
            raise ValueError("stale_pending_generation")
        if step["version"] != self.version:
            raise ValueError("stale_pending_version")
        self.version += 1
        self.sequences[step["participant"]] = step["sequence"]
        self.receipts[step["command"]] = {**step, "result": result}


@dataclass
class SessionModel:
    generation: int = 0
    active: str = "02"
    consumed: set[str] = field(default_factory=set)
    status: str = "active"

    def rotate(self, presented: str, replacement: str) -> None:
        if self.status != "active":
            raise ValueError("session_family_revoked")
        if presented in self.consumed:
            self.status = "refresh_replay"
            raise ValueError("refresh_replay_detected")
        if presented != self.active:
            raise ValueError("refresh_token_unknown")
        if replacement == self.active or replacement in self.consumed:
            raise ValueError("replacement_refresh_token_reused")
        self.consumed.add(self.active)
        self.active = replacement
        self.generation += 1


class FoundationVectorTests(unittest.TestCase):
    def test_authority_vectors(self) -> None:
        document = json.loads((ROOT / "contracts/foundation/authority-vectors.json").read_text())
        self.assertFalse(any(document["claims"].values()))
        for case in document["cases"]:
            with self.subTest(case=case["id"]):
                model = AuthorityModel()
                for step in case["steps"]:
                    op = step["op"]
                    try:
                        if op == "prepare_commit":
                            self.assertEqual(model.prepare(step), "ready")
                            model.commit(step, step["result"])
                        elif op == "prepare_only":
                            self.assertEqual(model.prepare(step), "ready")
                            model.pending[step["name"]] = step
                        elif op == "prepare":
                            self.assertEqual(model.prepare(step), step.get("expect", "ready"))
                        elif op == "takeover":
                            self.assertEqual(model.generation, step["expected_generation"])
                            model.generation += 1
                        elif op == "commit_pending":
                            model.commit(model.pending[step["name"]], step["result"])
                        else:
                            self.fail(f"unknown authority operation {op}")
                    except ValueError as exc:
                        self.assertEqual(str(exc), step.get("expect_error"))
                    else:
                        self.assertNotIn("expect_error", step)
                self.assertEqual(model.version, case["expect"]["version"])
                self.assertEqual(model.generation, case["expect"]["generation"])
                self.assertEqual(len(model.receipts), case["expect"]["receipts"])

    def test_session_vectors(self) -> None:
        document = json.loads((ROOT / "contracts/foundation/session-vectors.json").read_text())
        self.assertFalse(any(document["claims"].values()))
        for case in document["cases"]:
            with self.subTest(case=case["id"]):
                model = SessionModel()
                for step in case["steps"]:
                    try:
                        if step["op"] == "rotate":
                            model.rotate(step["presented"], step["replacement"])
                        elif step["op"] == "revoke":
                            model.status = step["reason"]
                        else:
                            self.fail(f"unknown session operation {step['op']}")
                    except ValueError as exc:
                        self.assertEqual(str(exc), step.get("expect_error"))
                    else:
                        self.assertNotIn("expect_error", step)
                self.assertEqual(model.generation, case["expect"]["generation"])
                self.assertEqual(model.active, case["expect"]["active"])
                self.assertEqual(model.status, case["expect"]["status"])


if __name__ == "__main__":
    unittest.main()
