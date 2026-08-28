#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "crates/trnm-transport-core/src/lib.rs"
LOCK = ROOT / "contracts/protocol/transport-error-source-lock.v1.json"
VECTORS = ROOT / "contracts/protocol/transport-error-vectors.v1.json"
STATUS = ROOT / "docs/status/TRANSPORT_ERROR_STATUS.json"


def fail(message: str) -> None:
    raise SystemExit(f"transport core contract failed: {message}")


def main() -> None:
    for path in (LIB, LOCK, VECTORS, STATUS):
        if not path.is_file():
            fail(f"missing {path.relative_to(ROOT)}")
    source = LIB.read_text(encoding="utf-8")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    status = json.loads(STATUS.read_text(encoding="utf-8"))

    for symbol in (
        "pub enum TransportPhase",
        "pub enum RealtimeContext",
        "pub enum RealtimeErrorCode",
        "pub enum WebSocketAction",
        "pub struct TransportMapping",
        "pub fn map_domain_error",
        "pub const fn http_status",
        "pub const fn grpc_code",
    ):
        if symbol not in source:
            fail(f"missing {symbol}")

    for pattern in (
        r"std::net",
        r"tokio",
        r"hyper::",
        r"tonic::",
        r"axum",
        r"tungstenite",
        r"serde",
        r"json",
        r"\bunsafe\b(?!_code)",
    ):
        if re.search(pattern, source):
            fail(f"transport pure core gained adapter capability {pattern}")
    if "private_reason_must_not_escape" not in source or "expose_internal_reason: false" not in source:
        fail("privacy regression test or suppression is missing")

    expected_rt = {
        "RUNTIME_EXCEPTION": 0,
        "UNRECOGNIZED_PAYLOAD": 1,
        "MISSING_PAYLOAD": 2,
        "BAD_INPUT": 3,
        "MATCH_NOT_FOUND": 4,
        "MATCH_JOIN_REJECTED": 5,
        "RUNTIME_FUNCTION_NOT_FOUND": 6,
        "RUNTIME_FUNCTION_EXCEPTION": 7,
    }
    if lock.get("realtime_error_codes") != expected_rt:
        fail("RTAPI error enum differs from reviewed lock")
    for artifact in (lock, vectors, status):
        if any(artifact.get("claims", {}).values()):
            fail("transport artifact overclaims maturity")
    if len(vectors.get("stable", [])) != 13 or len(vectors.get("contexts", [])) != 6:
        fail("transport vector matrix is incomplete")

    print(json.dumps({
        "status": "transport-error-static-contract-passed",
        "rust_test_contracts": source.count("#[test]"),
        "stable_vectors": len(vectors["stable"]),
        "context_vectors": len(vectors["contexts"]),
        "c1_earned": False,
        "production_ready": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
