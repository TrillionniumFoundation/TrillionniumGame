#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs"
WORKER = ROOT / "crates/trnm-persistence-pg/src/bin/trnm-outbox-worker.rs"
AUTHORITY = ROOT / "docs/development/CRYPTO_PATH_AUTHORITY.json"
def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)
app = APP.read_text(encoding="utf-8")
worker = WORKER.read_text(encoding="utf-8")
require("fn constant_time_eq" not in app, "custom app comparison helper is forbidden")
require("memcmp::eq(" in app, "app must use OpenSSL constant-time comparison")
require("fn constant_time_eq" not in worker, "custom outbox comparison helper is forbidden")
require("trnm_token_jwt_adapter::sha256_digest" not in worker, "outbox must not reach private JWT SHA")
require("hash(MessageDigest::sha256()" in worker, "outbox must use a reviewed SHA-256 provider")
authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
rows = {row["id"]: row for row in authority["path_classifications"]}
require(rows["CRYPTO-PATH-ACTIVE-OUTBOX-DIGEST"]["private_primitive_reachable"] is False, "outbox authority is stale")
require(authority["summary"]["gap_closed"] is False, "source cannot self-close security review")
print("production crypto path validation passed")
