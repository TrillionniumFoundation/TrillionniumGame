from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from tools.oracle.normalize import (
    assert_no_raw_tokens,
    decode_jwt_payload,
    load_registry,
    normalize_json,
    normalize_jwt,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config/oracle-normalizers.json"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compact_jwt(payload: dict[str, object]) -> str:
    encode = lambda value: base64.urlsafe_b64encode(canonical(value)).decode().rstrip("=")
    return f"{encode({'alg':'HS256','typ':'JWT'})}.{encode(payload)}.c2lnbmF0dXJl"


class NormalizerRegistryTests(unittest.TestCase):
    def test_registry_loads_and_keeps_claims_candidate_only(self) -> None:
        registry = load_registry(REGISTRY)
        self.assertEqual(registry["status"], "candidate-reviewed-required")
        self.assertEqual(len(registry["allowed"]), 6)
        self.assertFalse(any(registry["policy"].values()))

    def test_registry_rejects_forbidden_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            value = json.loads(REGISTRY.read_text(encoding="utf-8"))
            value["allowed"].append({"surface": "account", "path": "$.user.id", "reason": "bad"})
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_registry(path)

    def test_registry_rejects_duplicate_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            value = json.loads(REGISTRY.read_text(encoding="utf-8"))
            value["allowed"].append(dict(value["allowed"][0]))
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_registry(path)

    def test_jwt_temporal_claims_are_removed_but_identity_is_retained(self) -> None:
        registry = load_registry(REGISTRY)
        token = compact_jwt({"uid": "fixed", "usn": "user", "iat": 100, "exp": 200})
        normalized = normalize_jwt(decode_jwt_payload(token), "jwt-access", registry)
        self.assertEqual(normalized, {"uid": "fixed", "usn": "user"})

    def test_account_time_fields_are_removed_only(self) -> None:
        registry = load_registry(REGISTRY)
        account = {"user": {"id": "fixed", "username": "u", "create_time": "1", "update_time": "2"}}
        self.assertEqual(normalize_json(account, "account", registry), {"user": {"id": "fixed", "username": "u"}})

    def test_raw_token_fields_and_jwt_shaped_values_are_rejected(self) -> None:
        token = compact_jwt({"uid": "fixed"})
        for value in ({"access_token": token}, {"sessionToken": token}, {"nested": [token]}):
            with self.assertRaises(ValueError):
                assert_no_raw_tokens(value)


if __name__ == "__main__":
    unittest.main()
