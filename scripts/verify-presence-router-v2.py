#!/usr/bin/env python3
"""Fail-closed source verifier for the canonical presence router v2."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/realtime/presence-router-v2.json"
CRATE = ROOT / "crates/trnm-presence-router-v2"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read(path: Path) -> str:
    require(path.is_file(), f"missing required file: {path.relative_to(ROOT)}")
    value = path.read_text(encoding="utf-8")
    require(value.endswith("\n"), f"missing final newline: {path.relative_to(ROOT)}")
    require("\r" not in value, f"CRLF is forbidden: {path.relative_to(ROOT)}")
    return value


def public_signature(source: str, name: str) -> str:
    match = re.search(
        rf"pub\s+fn\s+{re.escape(name)}\s*\((.*?)\)\s*->",
        source,
        flags=re.S,
    )
    require(match is not None, f"missing public method: {name}")
    signature = re.sub(r"\s+", " ", match.group(1)).strip()
    return signature.removesuffix(",").strip()


def verify() -> dict[str, object]:
    contract = json.loads(read(CONTRACT))
    require(
        contract.get("schema") == "trillionnium.game.presence-router.v2",
        "wrong contract schema",
    )
    require(contract.get("version") == 2, "wrong contract version")
    require(
        contract.get("canonical_crate") == "crates/trnm-presence-router-v2",
        "canonical crate drift",
    )

    cargo = read(CRATE / "Cargo.toml")
    library = read(CRATE / "src/lib.rs")
    router = read(CRATE / "src/router.rs")
    types = read(CRATE / "src/types.rs")
    tests = read(CRATE / "tests/black_box.rs")

    require('rust-version = "1.85"' in cargo, "Rust MSRV is not pinned to 1.85")
    require("#![forbid(unsafe_code)]" in library, "unsafe code is not forbidden")
    all_rust = library + router + types + tests
    require(
        re.search(r"\bunsafe\b", all_rust.replace("unsafe_code", "")) is None,
        "unsafe token found",
    )

    expected_apis = {
        "join_presence": "&mut self, request: JoinPresenceRequest",
        "update_presence": "&mut self, request: UpdatePresenceRequest",
        "leave_presence": "&mut self, request: LeavePresenceRequest",
        "remove_connection": "&mut self, request: RemoveConnectionRequest",
    }
    for name, expected in expected_apis.items():
        signature = public_signature(router, name)
        require(signature == expected, f"{name} positional API drift: {signature!r}")

    require("struct ConnectionState" in router, "connection high-water state missing")
    require("generation: ConnectionGeneration" in router, "generation high-water missing")
    require("identity: PresenceIdentity" in router, "generation-bound identity missing")
    require(
        "connections: BTreeMap<ConnectionRef, ConnectionState>" in router,
        "router does not retain connection state",
    )
    require(
        "request.identity != state.identity" in router,
        "same-generation identity conflict check missing",
    )
    require(
        "records_for_connection(&request.connection)?" in router,
        "higher-generation retirement preflight missing",
    )
    require(
        "record identity differs from generation-bound identity" in router,
        "identity invariant check missing",
    )

    limits = contract["validated_limits"]
    expected_limits = {
        "MAX_CONNECTION_ID_BYTES": limits["connection_id_max_bytes"],
        "MAX_NODE_ID_BYTES": limits["node_id_max_bytes"],
        "MAX_USER_ID_BYTES": limits["user_id_max_bytes"],
        "MAX_SESSION_ID_BYTES": limits["session_id_max_bytes"],
        "MAX_USERNAME_BYTES": limits["username_max_bytes"],
        "MAX_STATUS_BYTES": limits["status_max_bytes"],
    }
    for constant, value in expected_limits.items():
        require(
            re.search(rf"pub const {constant}: usize = {value};", types) is not None,
            f"limit drift: {constant}",
        )
    require(
        "pub const MAX_STREAM_LABEL_BYTES: usize = 256;" in types,
        "stream label limit drift",
    )
    require(
        "if value == 0" in types and "ZeroGeneration" in types,
        "zero generation guard missing",
    )
    require(
        "if mode == 0" in types and "InvalidStreamMode" in types,
        "zero stream mode guard missing",
    )
    require(
        "value.chars().any(char::is_control)" in types,
        "control-character guard missing",
    )

    required_tests = [
        "identity_remains_bound_after_last_stream_is_left",
        "remove_connection_keeps_generation_and_identity_tombstone",
        "higher_generation_atomically_retires_all_old_streams",
        "stale_and_future_non_join_mutations_are_rejected_atomically",
        "snapshots_are_sorted_and_filter_hidden_records",
        "validated_types_reject_zero_empty_control_and_oversize_inputs",
    ]
    for test_name in required_tests:
        require(f"fn {test_name}" in tests, f"required black-box test missing: {test_name}")

    forbidden = set(contract["forbidden"])
    for item in (
        "positional_join_api_with_many_arguments",
        "same_generation_identity_replacement",
        "identity_reset_after_last_leave",
        "partial_generation_replacement",
        "stale_mutation_advances_revision",
        "nondeterministic_snapshot_order",
    ):
        require(item in forbidden, f"forbidden behavior omitted: {item}")

    return {
        "schema": "trillionnium.game.presence-router-verification.v2",
        "canonical_crate": "crates/trnm-presence-router-v2",
        "checks": {
            "typed_request_apis": True,
            "identity_bound_to_generation": True,
            "tombstone_retention": True,
            "higher_generation_preflight": True,
            "bounded_value_types": True,
            "deterministic_snapshot_contract": True,
            "unsafe_absent": True,
        },
        "claims": {
            "static_contract_passed": True,
            "rust_gate_passed": False,
            "distributed_fault_matrix_complete": False,
            "nakama_wire_compatibility_verified": False,
            "production_ready": False,
        },
    }


def main() -> int:
    try:
        report = verify()
    except (OSError, KeyError, TypeError, ValueError, VerificationError) as error:
        print(f"presence-router-v2 verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
