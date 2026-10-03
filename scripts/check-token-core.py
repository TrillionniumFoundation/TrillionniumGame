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
# Independent closed pins: preserve the original policy trio and explicitly
# enumerate six later identity-only auth/cache/transaction source bindings.
LEGACY_SOURCE_BLOBS = {'server/api_authenticate.go': '1f938603160ef1dc7f6546926de5481622139dd2', 'server/jwt.go': 'ab0c53aef5152429370ffe1d3ec9d273007132be', 'server/api_session.go': '1cef7b9d967e93745b19bdd048ff203fc212acea'}
ADDITIONAL_SOURCE_BLOBS = {'server/api.go': '61d1b8763f7b7fcf6ed0bc5cd720ff2314c7dacb', 'server/core_authenticate.go': '5ec2f5c00ef875d11fc79865a30059d1147ac7e1', 'server/core_session.go': 'beee140dd6d402434811721f858e64c1c3c79e09', 'server/session_cache.go': '2f3d4153bfadd6ad398d24173fe4413c12bf9b04', 'server/config.go': 'd9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6', 'server/db.go': '80bff94cfa224bc953fd674af673944ebc50e875'}
EXPECTED_UPSTREAM = {'repository': 'heroiclabs/nakama', 'tag': 'v3.40.0', 'commit': 'd4d92f93f78bbbe62c7fc50a3f85c772ec121a09', 'tree': 'f3c9cfc2726d5543da1564629170f35b98e3797d', 'server_tree': '7e93ec2a475f57fdeb041efe40a1b0db45b3cd81'}
EXPECTED_OBSERVED_CONTRACT = {'server/api_authenticate.go': ('tid', 'uid', 'usn', 'vrs', 'exp', 'iat'), 'server/jwt.go': ('HS256', 'expiration-required', 'valid-method-restriction'), 'server/api_session.go': ('refresh-required', 'same-token-id-refresh', 'session-cache-validation'), 'server/api.go': ('Bearer-purpose-key', 'UUID-parse', 'session-cache-before-context'), 'server/core_authenticate.go': ('Device-lookup-before-create', 'deferred-account-UUID', 'native-transaction'), 'server/core_session.go': ('stored-username-refresh', 'disable-Unix-seconds', 'ordered-purpose-owner-logout'), 'server/session_cache.go': ('blacklist-only', 'clock-before-lock', 'Add-Unban-no-op'), 'server/config.go': ('nonempty-distinct-session-keys', 'positive-TTLs', 'explicit-default20-and27'), 'server/db.go': ('PostgreSQL-class40-bounded-attempts', 'Cockroach-savepoint-retry', 'completion-error-boundary')}
EXPECTED_VERIFIED_FILES = {'server/api.go': ('61d1b8763f7b7fcf6ed0bc5cd720ff2314c7dacb', 30888, '583942e1fa47902a1b6cd231a08af3c938314cbe665e6a9765c3dc1959b6cae9'), 'server/api_authenticate.go': ('1f938603160ef1dc7f6546926de5481622139dd2', 33798, '89c0659aa6994c353e9d9dc32ccc412cbdb5dfc04537a1c93fa026dd32a06995'), 'server/api_session.go': ('1cef7b9d967e93745b19bdd048ff203fc212acea', 5848, '8bd5ad085d3ebf2a6accc3e8133f605a1f74fa899068caaca95514d321bdf090'), 'server/config.go': ('d9cd2b5c1bca3ae13a2560513a8fd99575ec4fe6', 72466, 'd437669975abe45ddaa8db556260e5c80faac69a1a4e9c2627967bdbfca24823'), 'server/core_authenticate.go': ('5ec2f5c00ef875d11fc79865a30059d1147ac7e1', 50169, '1995cf76a7e2f35185e5a9eef161da2cb5851be7a8bdeddb2609c1a0b7b4dc96'), 'server/core_session.go': ('beee140dd6d402434811721f858e64c1c3c79e09', 3668, '068b8846dc32321dc874c6cd0693e304042c08e8f75f9e50738254afa227546f'), 'server/db.go': ('80bff94cfa224bc953fd674af673944ebc50e875', 18795, '5ea00517ee4b9df752cd40da58e75bf18340bf587a9dd7ca4aaf9cef6c0f472d'), 'server/jwt.go': ('ab0c53aef5152429370ffe1d3ec9d273007132be', 1241, 'f50b8ced87d8b457714ee189a6dfdcee37e78fb57dfbd0b93ea9aade789f8cd2'), 'server/session_cache.go': ('2f3d4153bfadd6ad398d24173fe4413c12bf9b04', 6227, 'c7cc40783688ae48b677f3cca72f5afd7eefb8ab4ba0c6e81e3a6ee0ca2964e6')}
EXPECTED_FALSE_CLAIMS = {'production_ready', 'token_serialization_compatible', 'c1_earned', 'signature_compatible', 'refresh_behavior_compatible'}


def fail(message: str) -> None:
    raise SystemExit(f"token core contract failed: {message}")


def decode_source_lock(raw: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for name,value in pairs:
            if name in result:
                fail("duplicate source lock JSON field")
            result[name] = value
        return result
    def reject_constant(_value: str) -> None:
        fail("nonfinite source lock JSON value")
    if len(raw.encode("utf-8")) > 1024 * 1024:
        fail("source lock exceeds reviewed byte budget")
    try:
        return json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject_constant)
    except (ValueError,UnicodeError):
        fail("source lock JSON is invalid")


def validate_source_lock(lock: object) -> None:
    if not isinstance(lock, dict) or lock.get("schema") != "trillionnium.nakama-token-source-lock.v1" or lock.get("project_id") != "trillionnium-game" or lock.get("status") != "exact-source-observed":
        fail("upstream source lock envelope differs")
    if lock.get("upstream") != EXPECTED_UPSTREAM:
        fail("upstream source commit/tree identity differs")
    expected = {**LEGACY_SOURCE_BLOBS, **ADDITIONAL_SOURCE_BLOBS}
    rows = lock.get("sources")
    if not isinstance(rows, list) or len(rows) != len(expected):
        fail("upstream source list must retain exact reviewed nine files")
    observed = {}
    for item in rows:
        if not isinstance(item, dict) or set(item) != {"path", "blob", "observed_contract"}:
            fail("upstream source row shape differs")
        path = item["path"]
        if not isinstance(path, str) or path not in expected or path in observed or not isinstance(item["blob"], str) or item["blob"] != expected[path]:
            fail("upstream source path/blob missing, duplicated or changed")
        scope = item["observed_contract"]
        if not isinstance(scope, list) or any(not isinstance(value, str) for value in scope) or tuple(scope) != EXPECTED_OBSERVED_CONTRACT[path]:
            fail("upstream source observed contract differs")
        observed[path] = item["blob"]
    if observed != expected:
        fail("upstream source blobs differ from reviewed lock")
    verified = lock.get("verified_files")
    if not isinstance(verified, list) or len(verified) != len(EXPECTED_VERIFIED_FILES):
        fail("complete-file identity inventory differs")
    seen = set()
    for item in verified:
        if not isinstance(item, dict) or set(item) != {"path", "blob", "bytes", "sha256", "verification_scope", "primary_url"}:
            fail("complete-file identity row shape differs")
        path = item["path"]
        if not isinstance(path, str) or path not in EXPECTED_VERIFIED_FILES or path in seen:
            fail("complete-file identity path missing, duplicated or changed")
        seen.add(path)
        blob, size, digest = EXPECTED_VERIFIED_FILES[path]
        if type(item["bytes"]) is not int or item["bytes"] != size or not isinstance(item["blob"], str) or item["blob"] != blob or not isinstance(item["sha256"], str) or item["sha256"] != digest:
            fail("complete-file blob/size/SHA identity differs")
        expected_url = "https://github.com/heroiclabs/nakama/blob/" + EXPECTED_UPSTREAM["commit"] + "/" + path
        if item["verification_scope"] != "complete-file-identity-from-retained-official-API-bytes" or item["primary_url"] != expected_url:
            fail("complete-file attribution scope differs")
    claims = lock.get("claims")
    if not isinstance(claims, dict) or set(claims) != EXPECTED_FALSE_CLAIMS or any(value is not False for value in claims.values()):
        fail("token source lock claims differ or overclaim maturity")


def main() -> None:
    for path in (LIB, LOCK, VECTORS, STATUS):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")
    source = LIB.read_text(encoding="utf-8")
    lock = decode_source_lock(LOCK.read_text(encoding="utf-8"))
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
        r"\bhmac\b",
        r"\bsha2\b",
        r"\bopenssl\b",
        r"\bjsonwebtoken\b",
        r"\bsigned_string\b",
        r"\bsecret_key\b",
        r"\bkey_bytes\b",
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
    if re.search(r"\bkey_bytes\b", "max_var_key_bytes", re.IGNORECASE):
        fail("raw-key guard rejects bounded variable key length fields")
    if not re.search(r"\bkey_bytes\b", "let key_bytes = secret", re.IGNORECASE):
        fail("raw-key guard no longer detects a raw key identifier")
    for pattern in namespace_patterns:
        if re.search(pattern, source):
            fail(f"forbidden crypto/capability namespace {pattern}")

    validate_source_lock(lock)
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
