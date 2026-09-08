#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "crates/trnm-persistence-pg/src/auth.rs"
PROVIDER = ROOT / "crates/trnm-token-crypto-provider/src/software.rs"
MANIFEST = ROOT / "crates/trnm-persistence-pg/Cargo.toml"
AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)
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
print("active authentication provider composition validation passed")
