#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "crates/trnm-token-core/src/lib.rs"
LOCK = ROOT / "contracts/session/nakama-v340-token-source-lock.json"
VECTORS = ROOT / "contracts/session/token-policy-vectors.v1.json"
STATUS = ROOT / "docs/status/TOKEN_POLICY_STATUS.json"


def fail(message: str) -> None:
    raise SystemExit(f"token core contract failed: {message}")


def main() -> None:
    for path in (LIB, LOCK, VECTORS, STATUS):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")
    source = LIB.read_text(encoding="utf-8")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    status = json.loads(STATUS.read_text(encoding="utf-8"))

    for symbol in (
        "pub enum TokenProfile",
        "pub struct KeyDescriptor",
        "pub struct KeyRing",
        "pub struct TokenClaims",
        "pub struct SigningPlan",
        "pub struct VerificationPlan",
        "pub fn prepare_issue",
        "pub fn prepare_verification",
        "pub fn accept_verified_claims",
    ):
        if symbol not in source:
            fail(f"missing {symbol}")

    case_insensitive_patterns = (
        r"\bunsafe\b(?!_code)",
        r"std::net",
        r"std::time",
        r"SystemTime",
        r"rand::",
        r"hmac",
        r"sha2",
        r"openssl",
        r"jsonwebtoken",
        r"signed_string",
        r"secret_key",
        r"key_bytes",
    )
    for pattern in case_insensitive_patterns:
        if re.search(pattern, source, re.IGNORECASE):
            fail(f"forbidden crypto/capability pattern {pattern}")

    # Namespace checks are deliberately case-sensitive and boundary-aware. The
    # policy model owns a `KeyRing` type; matching its `KeyRing::...` calls as
    # the external `ring::...` crypto namespace would be a false positive.
    namespace_patterns = (
        r"(?<![A-Za-z0-9_])ring::",
        r"(?<![A-Za-z0-9_])openssl::",
        r"(?<![A-Za-z0-9_])jsonwebtoken::",
    )
    if re.search(namespace_patterns[0], "KeyRing::default()"):
        fail("ring namespace guard rejects the local KeyRing type")
    if not re.search(namespace_patterns[0], "ring::digest"):
        fail("ring namespace guard no longer detects the external crate")
    for pattern in namespace_patterns:
        if re.search(pattern, source):
            fail(f"forbidden crypto/capability namespace {pattern}")

    expected = {
        "server/api_authenticate.go": "1f938603160ef1dc7f6546926de5481622139dd2",
        "server/jwt.go": "ab0c53aef5152429370ffe1d3ec9d273007132be",
        "server/api_session.go": "1cef7b9d967e93745b19bdd048ff203fc212acea",
    }
    observed = {item["path"]: item["blob"] for item in lock["sources"]}
    if observed != expected:
        fail("upstream source blobs differ from reviewed lock")
    if any(lock["claims"].values()) or any(vectors["claims"].values()) or any(status["claims"].values()):
        fail("token artifacts overclaim maturity")
    if len(vectors["cases"]) < 8:
        fail("insufficient token policy vectors")
    if "material_digest" not in source:
        fail("key material must be represented only by digest reference")

    print(json.dumps({
        "status": "token-policy-static-contract-passed",
        "rust_test_contracts": source.count("#[test]"),
        "vector_cases": len(vectors["cases"]),
        "raw_key_handling_implemented": False,
        "signature_compatible": False,
        "production_ready": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
