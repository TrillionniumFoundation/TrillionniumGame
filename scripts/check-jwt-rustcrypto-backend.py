#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = [
    ROOT / "crates/trnm-token-jwt-adapter/Cargo.toml",
    ROOT / "crates/trnm-token-jwt-adapter-gate/Cargo.toml",
    ROOT / "crates/trnm-token-jwt-adapter-gate-v2/Cargo.toml",
]
SOURCE = ROOT / "crates/trnm-token-jwt-adapter/src/sha256.rs"
AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)
for manifest in MANIFESTS:
    text = manifest.read_text(encoding="utf-8")
    for dependency in ("hmac", "sha2", "subtle"):
        require(f"{dependency} =" in text, f"{manifest}: missing {dependency}")
source = SOURCE.read_text(encoding="utf-8")
for marker in ("Hmac<Sha256>", "Sha256::digest", "ConstantTimeEq", ".ct_eq("):
    require(marker in source, f"JWT backend missing {marker}")
for marker in ("ROUND_CONSTANTS", "wrapping_add", "rotate_right"):
    require(marker not in source, f"private SHA marker remains: {marker}")
authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
rows = {row["id"]: row for row in authority["path_classifications"]}
row = rows["CRYPTO-PATH-COMPATIBILITY-REFERENCE"]
require(row["private_primitive_reachable"] is False, "crypto authority is stale")
require(authority["summary"]["gap_closed"] is False, "library migration cannot self-close review")
print("JWT RustCrypto backend validation passed")
