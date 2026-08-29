#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "crates/trnm-presence-core/src/lib.rs"
LOCK = ROOT / "contracts/realtime/presence-source-lock.v1.json"
VECTORS = ROOT / "contracts/realtime/presence-vectors.v1.json"
STATUS = ROOT / "docs/status/PRESENCE_ROUTER_STATUS.json"


def fail(message: str) -> None:
    raise SystemExit(f"presence core contract failed: {message}")


def main() -> None:
    for path in (LIB, LOCK, VECTORS, STATUS):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")
    source = LIB.read_text(encoding="utf-8")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    status = json.loads(STATUS.read_text(encoding="utf-8"))

    for symbol in (
        "pub struct RouterState",
        "pub struct ConnectionRecord",
        "pub struct PresenceRecord",
        "pub struct JoinPresenceRequest",
        "pub struct RouteTarget",
        "pub fn open",
        "pub fn route",
        "pub fn rebind",
        "pub fn begin_node_drain",
        "pub fn join_presence",
        "pub fn reserve_outbound",
        "pub fn revoke_user",
        "pub fn expire_idle",
    ):
        if symbol not in source:
            fail(f"missing {symbol}")
    for pattern in (
        r"std::net",
        r"tokio",
        r"async_std",
        r"tungstenite",
        r"axum",
        r"hyper::",
        r"std::time",
        r"SystemTime",
        r"rand::",
        r"sqlx",
        r"\bunsafe\b(?!_code)",
    ):
        if re.search(pattern, source):
            fail(f"pure presence core gained forbidden capability {pattern}")

    expected = {
        "runtime/runtime.go": "da7f2f2ad41ef5061d48f2e037678bb8397cc045",
        "rtapi/realtime.proto": "b23efef88565e0e09b3f6ee7ed8e08e9d240e27d",
    }
    observed = {item["path"]: item["blob"] for item in lock["nakama_common"]["sources"]}
    if observed != expected:
        fail("presence source lock differs from reviewed blobs")
    if any(lock["claims"].values()) or any(vectors["claims"].values()) or any(status["claims"].values()):
        fail("presence artifacts overclaim maturity")
    if len(vectors["cases"]) < 8:
        fail("insufficient presence vectors")

    print(json.dumps({
        "status": "presence-router-static-contract-passed",
        "rust_test_contracts": source.count("#[test]"),
        "vector_cases": len(vectors["cases"]),
        "socket_implemented": False,
        "multi_node_verified": False,
        "production_ready": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
