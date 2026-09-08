from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
DOCUMENTATION_AUTHORITY = ROOT / "docs/DOCUMENTATION_AUTHORITY.json"
ACCESS_AUTH = ROOT / "crates/trnm-persistence-pg/src/auth.rs"
OUTBOX_WORKER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"


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


class CryptoPathAuthorityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.authority = load_object(AUTHORITY)
        cls.documentation_authority = load_object(DOCUMENTATION_AUTHORITY)
        cls.paths = {row["id"]: row for row in cls.authority["path_classifications"]}

    def test_registry_is_authoritative_but_does_not_grant_security_credit(self) -> None:
        self.assertEqual(self.authority["schema"], "trillionnium.crypto-path-authority.v1")
        self.assertIn(
            "docs/development/CRYPTO_PATH_AUTHORITY.json",
            self.documentation_authority["machine_control_documents"],
        )
        summary = self.authority["summary"]
        self.assertTrue(summary["classified_active_paths_use_reviewed_primitives"])
        self.assertFalse(summary["all_production_crypto_paths_classified_and_accepted"])
        self.assertFalse(summary["production_key_provider_accepted"])
        self.assertFalse(summary["private_compatibility_implementation_removed"])
        self.assertFalse(summary["gap_closed"])
        self.assertFalse(summary["production_ready"])

    def test_access_authentication_path_uses_openssl_and_not_private_mac(self) -> None:
        row = self.paths["CRYPTO-PATH-ACTIVE-ACCESS-AUTH"]
        self.assertFalse(row["private_primitive_reachable"])
        self.assertEqual(row["primitive_provider"], "PROVIDER-OPENSSL-SOFTWARE-CRYPTO")
        production = ACCESS_AUTH.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        self.assertIn("PKey::hmac", production)
        self.assertIn("Signer::new(MessageDigest::sha256()", production)
        self.assertIn("memcmp::eq", production)
        self.assertIn("hash(MessageDigest::sha256()", production)
        for forbidden in ("KeyRing", "SecretKey", "sha256_digest", "constant_time_eq"):
            self.assertNotIn(forbidden, production)

    def test_outbox_digest_uses_openssl_without_private_hash_or_comparator(self) -> None:
        row = self.paths["CRYPTO-PATH-ACTIVE-OUTBOX-DIGEST"]
        self.assertFalse(row["private_primitive_reachable"])
        self.assertEqual(row["primitive_provider"], "PROVIDER-OPENSSL-SOFTWARE-CRYPTO")
        source = OUTBOX_WORKER.read_text(encoding="utf-8")
        self.assertIn("hash(MessageDigest::sha256()", source)
        self.assertIn("memcmp::eq", source)
        self.assertIn("fn sha256_delivery", source)
        self.assertIn("fn sha256_worker", source)
        self.assertNotIn("trnm_token_jwt_adapter::sha256_digest", source)
        self.assertNotIn("fn constant_time_eq", source)
        self.assertIn("GAP-P0-CRYPTO-001", row["gap_links"])
        self.assertIn("GAP-P1-OUTBOX-001", row["gap_links"])

    def test_reference_adapter_cannot_be_misclassified_as_production_provider(self) -> None:
        row = self.paths["CRYPTO-PATH-COMPATIBILITY-REFERENCE"]
        self.assertTrue(row["private_primitive_reachable"])
        self.assertIn("non-production", row["classification"])
        self.assertIsNone(row["primitive_provider"])
        self.assertFalse(row["claim_credit"])

    def test_every_classified_path_resolves_and_has_gap_links(self) -> None:
        for row in self.paths.values():
            with self.subTest(path=row["id"]):
                self.assertTrue(row["entrypoints"])
                self.assertTrue(row["gap_links"])
                self.assertFalse(row["claim_credit"])
                for relative in row["entrypoints"]:
                    self.assertTrue((ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
