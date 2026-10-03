#!/usr/bin/env python3
from __future__ import annotations
import json
import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "crates/trnm-persistence-pg/src/auth.rs"
PROVIDER = ROOT / "crates/trnm-token-crypto-provider/src/software.rs"
MANIFEST = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
LEGACY_CRYPTO_SOURCE_ROW = {'id': 'CRYPTO-PATH-NAKAMA-LEGACY-SELECTED-AUTH-SOURCE', 'classification': 'selected-App-source-accounts5-capture-gated', 'entrypoints': ['crates/trnm-server/src/main.rs'], 'implementation': 'One selected AuthAuthorityRuntime owns the existing RustCrypto Legacy service and fixed-purpose keys; same-process workers share one blacklist.', 'provider_contract': 'CONTRACT-NAKAMA-LEGACY-FIXED-PURPOSE-SOURCE', 'primitive_provider': 'PROVIDER-RUSTCRYPTO-JWT', 'private_primitive_reachable': False, 'AccountsV5_gate': False, 'gap_links': ['GAP-P0-CRYPTO-001', 'GAP-P1-CRYPTO-002', 'GAP-P0-SERVER-001'], 'claim_credit': False, 'required_acceptance': 'Exact native HTTP and paired oracle, same-process lifecycle/fault evidence, production credential custody and independent cryptography/security acceptance.', 'required_remediation': 'Keep capture gate closed until separately qualified; no key fallback, implicit defaults or durable-family conversion.'}

def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)
def main() -> int:
    auth = AUTH.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
    provider = PROVIDER.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    for marker in ("trnm_token_jwt_provider_adapter", "authenticate(", "Hs256Provider", "from_provider("):
        require(marker in auth, f"active auth provider composition missing {marker}")
    for forbidden in ("PKey::hmac", "Signer::new", "hmac_sha256", "constant_time_eq"):
        require(forbidden not in auth, f"active auth directly owns primitive {forbidden}")
    for dependency in ("trnm-token-crypto-provider", "trnm-token-jwt-provider-adapter"):
        require(dependency in manifest, f"persistence dependency missing {dependency}")
    for marker in ("impl Hs256Provider for SoftwareHs256Provider", "HmacSha256", "ct_eq"):
        require(marker in provider, f"software provider missing {marker}")
    authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in authority["path_classifications"]}
    require(rows["CRYPTO-PATH-ACTIVE-ACCESS-AUTH"]["private_primitive_reachable"] is False, "active auth authority is stale")
    require(authority["summary"]["production_key_provider_accepted"] is False, "software source must not claim KMS/HSM acceptance")
    require(authority["summary"]["gap_closed"] is False, "source cannot self-close security review")
    rows_expected = {
        "CRYPTO-PATH-ACTIVE-ACCESS-AUTH", "CRYPTO-PATH-ACTIVE-ADMIN-AUTH",
        "CRYPTO-PATH-ACTIVE-OUTBOX-DIGEST", "CRYPTO-PATH-COMPATIBILITY-REFERENCE",
        "CRYPTO-PATH-NAKAMA-LEGACY-SELECTED-AUTH-SOURCE"}
    require(type(authority["path_classifications"]) is list
            and len(authority["path_classifications"]) == len(rows_expected)
            and set(rows) == rows_expected and type(authority["summary"]["classified_paths"]) is int
            and authority["summary"]["classified_paths"] == 5, "crypto path inventory drift")
    legacy = rows["CRYPTO-PATH-NAKAMA-LEGACY-SELECTED-AUTH-SOURCE"]
    require(legacy["AccountsV5_gate"] is False and legacy["claim_credit"] is False
            and legacy["private_primitive_reachable"] is False
            and legacy["primitive_provider"] == "PROVIDER-RUSTCRYPTO-JWT",
            "selected Legacy crypto path cannot grant native or security credit")
    expected_keys = {'hs256_minimum_actual_key_bytes': 32, 'encoded_length_may_substitute': False, 'padding_hashing_or_truncation_may_substitute': False, 'hs256_minimum_scope': 'durable software HS256 profile only', 'selected_legacy_key_bytes_min': 1, 'selected_legacy_key_bytes_max': 4096, 'selected_legacy_implicit_keys_or_TTLs': False, 'selected_legacy_equal_access_refresh_keys_rejected_inherited_local_policy': True}
    require(type(authority["key_contracts"]) is dict
            and all(type(authority["key_contracts"].get(k)) is type(v)
                    and authority["key_contracts"].get(k) == v for k,v in expected_keys.items())
            and set(authority["key_contracts"]) == set(expected_keys), "key profile scope drift")
    spec = importlib.util.spec_from_file_location("auth_selected_composition",
                                                 ROOT / "scripts/check-trnm-server.py")
    require(spec is not None and spec.loader is not None, "source contract loader missing")
    selected = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selected)
    require(selected.same_typed_value(legacy, LEGACY_CRYPTO_SOURCE_ROW),
            "selected Legacy crypto scope, entrypoint or identity drift")
    sources = {Path(path): (ROOT / path).read_text() for path in selected.SELECTED_AUTH_APP_FULL_SOURCE_SHA256}
    selected.validate_selected_auth_app_source(sources,
        json.loads((ROOT / "docs/status/TRNM_SERVER_STATUS.json").read_text()))
    print("active and selected-source authentication provider composition validation passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
