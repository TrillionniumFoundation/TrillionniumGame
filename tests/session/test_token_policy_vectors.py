from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "contracts/session/token-policy-vectors.v1.json"


class PolicyError(RuntimeError):
    pass


def eligible(key: dict[str, object], now: int, issue: bool) -> bool:
    statuses = {"active"} if issue else {"active", "verify-only"}
    return key["status"] in statuses and int(key["not_before"]) <= now < int(key["not_after"])


def select(case: dict[str, object], issue: bool) -> int:
    now = int(case["now"])
    keys = [key for key in case["keys"] if eligible(key, now, issue)]
    profile = case["profile"]
    declared = case.get("declared_epoch")
    if issue:
        if len(keys) != 1:
            raise PolicyError("token_issue_key_unavailable" if not keys else "ambiguous_active_token_key")
        return int(keys[0]["epoch"])
    if profile == "legacy":
        if declared is not None:
            raise PolicyError("legacy_token_must_not_declare_key_epoch")
        if len(keys) != 1:
            raise PolicyError("token_verify_key_unavailable" if not keys else "ambiguous_legacy_key_epoch")
        return int(keys[0]["epoch"])
    if declared is None:
        raise PolicyError("token_key_epoch_required")
    for key in keys:
        if int(key["epoch"]) == int(declared):
            return int(declared)
    if any(int(key["epoch"]) == int(declared) for key in case["keys"]):
        raise PolicyError("token_key_unavailable")
    raise PolicyError("token_key_not_found")


def execute(case: dict[str, object]) -> dict[str, object]:
    operation = case["operation"]
    if operation == "issue":
        epoch = select(case, True)
        claims = case["claims"]
        if case["profile"] == "family-v1" and claims["epoch"] != epoch:
            raise PolicyError("token_claim_key_epoch_mismatch")
        return {"selected_epoch": epoch, "emitted_epoch": None if case["profile"] == "legacy" else epoch}
    epoch = select(case, False)
    if operation == "verify-key":
        return {"selected_epoch": epoch}
    claims = case["claims"]
    now = int(case["now"])
    if int(claims["issued_at"]) > now + 30:
        raise PolicyError("token_issued_in_future")
    if now >= int(claims["expires_at"]) + 30:
        raise PolicyError("token_expired")
    if case["profile"] == "family-v1" and claims["epoch"] != epoch:
        raise PolicyError("token_claim_key_epoch_mismatch")
    return {"selected_epoch": epoch}


class TokenPolicyVectorTests(unittest.TestCase):
    def test_vectors(self) -> None:
        document = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "trillionnium.token-policy-vectors.v1")
        self.assertGreaterEqual(len(document["cases"]), 8)
        self.assertFalse(any(document["claims"].values()))
        for case in document["cases"]:
            try:
                result = execute(case)
            except PolicyError as error:
                self.assertEqual(str(error), case.get("expect_error"), case["id"])
            else:
                self.assertNotIn("expect_error", case, case["id"])
                self.assertEqual(result, case["expect"], case["id"])


if __name__ == "__main__":
    unittest.main()
