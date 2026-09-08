#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

REMOVED_ONE_SHOTS = {
    ".github/workflows/architecture-source-export-one-shot.yml",
    ".github/workflows/architecture-test-name-one-shot.yml",
}


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(read(path))
    require(isinstance(value, dict), f"{path}: object required")
    return value


def dump(path: Path, value: dict[str, Any]) -> None:
    write(path, json.dumps(value, indent=2, ensure_ascii=False))


def update_server_slice_checker(root: Path) -> None:
    path = root / "scripts/check-rust-server-slice.py"
    text = read(path)
    text = text.replace(
        'source_paths = [CRATE / "src/lib.rs", CANDIDATE_SERVER] + sorted(',
        'source_paths = [CRATE / "src/lib.rs", CANONICAL_SERVER] + sorted(',
    )
    text = text.replace(
        'require(CANONICAL_SERVER.is_file(), "current canonical persistence-owned server is missing")',
        'require(CANONICAL_SERVER.is_file(), "canonical trnm-server composition root is missing")',
    )
    text = text.replace(
        'require(CANDIDATE_SERVER.is_file(), "package-local composition candidate is missing")',
        'require(CANDIDATE_SERVER.is_file(), "diagnostic persistence compatibility server is missing")',
    )
    text = text.replace(
        'require(\'name = "trnm-server-composition-candidate"\\npath = "src/main.rs"\' in manifest, "candidate binary binding missing")',
        'require(\'name = "trnm-server"\\npath = "src/main.rs"\' in manifest, "canonical binary binding missing")',
    )
    text = text.replace(
        '"candidate_server": str(CANDIDATE_SERVER.relative_to(ROOT)),',
        '"diagnostic_server": str(CANDIDATE_SERVER.relative_to(ROOT)),',
    )
    text = text.replace(
        '"authority_transferred": False,',
        '"authority_transferred_source_candidate": True,',
    )
    write(path, text)


def update_architecture_test(root: Path) -> None:
    path = root / "tests/control_plane/test_architecture_closure.py"
    text = read(path)
    replacement = '''    def test_active_server_auth_uses_reviewed_crypto_boundary(self) -> None:
        production = AUTH_SOURCE.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        for required in (
            "Arc<dyn Hs256Provider>",
            "impl KeyResolver for FixedAccessKeyResolver",
            "AuthenticationProfile",
            "authenticate(",
            "from_provider(",
            "SoftwareHs256Provider",
            "hash(MessageDigest::sha256()",
        ):
            self.assertIn(required, production)
        for forbidden in (
            "PKey::hmac",
            "Signer::new",
            "KeyRing",
            "trnm_token_jwt_adapter::sha256_digest",
            "constant_time_eq",
        ):
            self.assertNotIn(forbidden, production)

'''
    text, count = re.subn(
        r"(?ms)^    def test_active_server_auth_uses_reviewed_crypto_boundary\(self\) -> None:\n.*?(?=^    def test_external_facts_cannot_be_auto_closed)",
        replacement,
        text,
        count=1,
    )
    require(count == 1, "architecture crypto test replacement failed")
    write(path, text)


def update_crypto_authority(root: Path) -> None:
    path = root / "docs/development/CRYPTO_PATH_AUTHORITY.json"
    value = load(path)
    rows = {
        row["id"]: row
        for row in value.get("path_classifications", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    access = rows["CRYPTO-PATH-ACTIVE-ACCESS-AUTH"]
    access.update(
        {
            "classification": "active-server-authentication-opaque-provider-source-candidate",
            "implementation": "trnm-token-jwt-provider-adapter composes AuthenticationProfile, KeyResolver and Hs256Provider before claims parsing",
            "provider_contract": "CONTRACT-HS256-OPAQUE-PROVIDER",
            "primitive_provider": "CONTRACT-HS256-OPAQUE-PROVIDER",
            "opaque_provider_composed": True,
            "private_primitive_reachable": False,
            "required_remediation": "Replace the development software provider with an accepted KMS or HSM provider and retain rotation, revoke, timeout, cancellation and audit evidence.",
            "claim_credit": False,
        }
    )
    outbox = rows["CRYPTO-PATH-ACTIVE-OUTBOX-DIGEST"]
    outbox.update(
        {
            "classification": "active-durable-spool-reviewed-primitive-source-candidate",
            "implementation": "OpenSSL SHA-256 and constant-time comparison over byte-stable spool and retry material",
            "primitive_provider": "PROVIDER-OPENSSL-SOFTWARE-CRYPTO",
            "private_primitive_reachable": False,
            "required_remediation": "Retain byte-stable receipt and retry-jitter fault evidence, then obtain independent data-integrity and security acceptance.",
            "claim_credit": False,
        }
    )
    reference = rows["CRYPTO-PATH-COMPATIBILITY-REFERENCE"]
    reference.update(
        {
            "classification": "non-production-compatibility-adapter-reviewed-library-source-candidate",
            "implementation": "strict JWT adapter backed by RustCrypto hmac and sha2 plus subtle constant-time comparison",
            "primitive_provider": "PROVIDER-RUSTCRYPTO-JWT",
            "private_primitive_reachable": False,
            "required_remediation": "Retain three-way Nakama differential, malformed-token and fuzz evidence plus independent cryptographic acceptance.",
            "claim_credit": False,
        }
    )
    summary = value.setdefault("summary", {})
    summary.update(
        {
            "classified_paths": len(rows),
            "active_access_opaque_provider_composed": True,
            "active_access_auth_private_primitive_reachable": False,
            "active_admin_auth_private_primitive_reachable": False,
            "active_outbox_digest_private_primitive_reachable": False,
            "classified_active_paths_use_reviewed_primitives": True,
            "all_production_crypto_paths_classified_and_accepted": False,
            "production_key_provider_accepted": False,
            "private_compatibility_implementation_removed": True,
            "gap_closed": False,
            "production_ready": False,
        }
    )
    dump(path, value)


def update_crypto_test(root: Path) -> None:
    path = root / "tests/control_plane/test_crypto_path_authority.py"
    write(
        path,
        '''from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
DOCUMENTATION_AUTHORITY = ROOT / "docs/DOCUMENTATION_AUTHORITY.json"
ACCESS_AUTH = ROOT / "crates/trnm-persistence-pg/src/auth.rs"
PERSISTENCE_MANIFEST = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
ADMIN_APP = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs"
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
        self.assertIn("docs/development/CRYPTO_PATH_AUTHORITY.json", self.documentation_authority["machine_control_documents"])
        summary = self.authority["summary"]
        self.assertEqual(summary["classified_paths"], len(self.paths))
        self.assertTrue(summary["active_access_opaque_provider_composed"])
        self.assertTrue(summary["classified_active_paths_use_reviewed_primitives"])
        self.assertFalse(summary["active_access_auth_private_primitive_reachable"])
        self.assertFalse(summary["active_admin_auth_private_primitive_reachable"])
        self.assertFalse(summary["active_outbox_digest_private_primitive_reachable"])
        self.assertTrue(summary["private_compatibility_implementation_removed"])
        self.assertFalse(summary["all_production_crypto_paths_classified_and_accepted"])
        self.assertFalse(summary["production_key_provider_accepted"])
        self.assertFalse(summary["gap_closed"])
        self.assertFalse(summary["production_ready"])

    def test_access_authentication_composes_opaque_provider_and_resolver(self) -> None:
        row = self.paths["CRYPTO-PATH-ACTIVE-ACCESS-AUTH"]
        self.assertTrue(row["opaque_provider_composed"])
        self.assertFalse(row["private_primitive_reachable"])
        self.assertEqual(row["provider_contract"], "CONTRACT-HS256-OPAQUE-PROVIDER")
        self.assertEqual(row["primitive_provider"], "CONTRACT-HS256-OPAQUE-PROVIDER")
        production = ACCESS_AUTH.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
        manifest = PERSISTENCE_MANIFEST.read_text(encoding="utf-8")
        for required in (
            "Arc<dyn Hs256Provider>",
            "impl KeyResolver for FixedAccessKeyResolver",
            "AuthenticationProfile",
            "authenticate(",
            "from_provider(",
            "SoftwareHs256Provider",
        ):
            self.assertIn(required, production)
        self.assertIn("trnm-token-crypto-provider", manifest)
        self.assertIn("trnm-token-jwt-provider-adapter", manifest)
        for forbidden in ("PKey::hmac", "Signer::new", "KeyRing", "SecretKey", "constant_time_eq"):
            self.assertNotIn(forbidden, production)

    def test_administrator_token_comparison_uses_openssl(self) -> None:
        row = self.paths["CRYPTO-PATH-ACTIVE-ADMIN-AUTH"]
        self.assertFalse(row["private_primitive_reachable"])
        self.assertEqual(row["primitive_provider"], "PROVIDER-OPENSSL-SOFTWARE-CRYPTO")
        source = ADMIN_APP.read_text(encoding="utf-8")
        self.assertIn("use openssl::memcmp", source)
        self.assertIn("fn secure_token_eq", source)
        self.assertIn("memcmp::eq(left, right)", source)
        self.assertIn("admin_token_comparison_rejects_a_256_byte_length_delta", source)
        self.assertNotIn("fn constant_time_eq", source)

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

    def test_reference_adapter_uses_reviewed_libraries_but_is_not_production(self) -> None:
        row = self.paths["CRYPTO-PATH-COMPATIBILITY-REFERENCE"]
        self.assertFalse(row["private_primitive_reachable"])
        self.assertIn("non-production", row["classification"])
        self.assertEqual(row["primitive_provider"], "PROVIDER-RUSTCRYPTO-JWT")
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
''',
    )


def align_outbox_checker(root: Path) -> None:
    worker = root / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"
    write(worker, read(worker).replace("use openssl::sha::sha256;\n\n", ""))
    checker = root / "scripts/check-production-crypto-paths.py"
    text = read(checker).replace(
        'require("openssl::sha::sha256" in worker, "outbox must use a reviewed SHA-256 provider")',
        'require("hash(MessageDigest::sha256()" in worker, "outbox must use a reviewed SHA-256 provider")',
    )
    write(checker, text)


def blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def update_workflow_manifest(root: Path) -> None:
    path = root / "docs/governance/REQUIRED_WORKFLOWS_V1.json"
    value = load(path)
    aggregate = value.get("aggregate_workflow")
    require(isinstance(aggregate, dict), "aggregate workflow missing")
    aggregate_path = aggregate.get("path")
    require(isinstance(aggregate_path, str), "aggregate path missing")
    aggregate_file = root / aggregate_path
    require(aggregate_file.is_file(), f"missing aggregate workflow: {aggregate_path}")
    aggregate["git_blob_sha1"] = blob_sha1(aggregate_file)
    workflows = value.get("workflows")
    require(isinstance(workflows, list), "workflow list missing")
    retained = []
    for row in workflows:
        require(isinstance(row, dict), "workflow row must be object")
        relative = row.get("path")
        require(isinstance(relative, str), "workflow path missing")
        if relative in REMOVED_ONE_SHOTS:
            continue
        workflow = root / relative
        require(workflow.is_file(), f"required workflow missing: {relative}")
        row["git_blob_sha1"] = blob_sha1(workflow)
        retained.append(row)
    value["workflows"] = retained
    dump(path, value)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    update_server_slice_checker(root)
    update_architecture_test(root)
    update_crypto_authority(root)
    update_crypto_test(root)
    align_outbox_checker(root)
    update_workflow_manifest(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
