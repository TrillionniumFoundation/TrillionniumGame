#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import struct
import sys
from typing import Any

CORE_STORAGE_SCHEMA = "trnm.nakama.stored_match.v1"
CORE_SNAPSHOT_SCHEMA = "trnm.match.snapshot.v2"
WORLD_STORAGE_SCHEMA = "trnm.game.world-command-storage.v1"
WORLD_SNAPSHOT_SCHEMA = "trnm.nakama.world-command-store.v1"
CORE_MAGIC = b"TRNMSNP2"


def fail(message: str) -> None:
    raise SystemExit(message)


def read_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        fail(f"{label} is not valid JSON: {error}")
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def canonical_base64(value: Any, label: str, maximum: int) -> bytes:
    if not isinstance(value, str) or not value:
        fail(f"{label} must be a non-empty base64 string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as error:
        fail(f"{label} is not canonical base64: {error}")
    if base64.b64encode(decoded).decode("ascii") != value:
        fail(f"{label} is not canonical padded base64")
    if len(decoded) == 0 or len(decoded) > maximum:
        fail(f"{label} has an invalid decoded size")
    return decoded


def require_sha256(value: Any, payload: bytes, label: str) -> None:
    expected = hashlib.sha256(payload).hexdigest()
    if value != expected:
        fail(f"{label} checksum mismatch")


def unpack_core(record: dict[str, Any], logical_match_id: str) -> dict[str, Any]:
    if set(record) != {
        "schema",
        "logical_match_id",
        "core_snapshot_base64",
        "snapshot_sha256",
        "external_match_id",
        "runtime_generation",
    }:
        fail(f"core storage field set drift: {sorted(record)}")
    if record.get("schema") != CORE_STORAGE_SCHEMA or record.get("logical_match_id") != logical_match_id:
        fail("core storage identity mismatch")
    snapshot = canonical_base64(record.get("core_snapshot_base64"), "core snapshot", 65 * 1024 * 1024)
    require_sha256(record.get("snapshot_sha256"), snapshot, "core storage")
    if len(snapshot) < 8 + 8 + 32 + 64 or snapshot[:8] != CORE_MAGIC:
        fail("core snapshot envelope header is invalid")
    payload_size = struct.unpack(">Q", snapshot[8:16])[0]
    payload_end = 16 + payload_size
    if payload_size > 64 * 1024 * 1024 or payload_end + 32 + 64 != len(snapshot):
        fail("core snapshot envelope length is invalid")
    payload = snapshot[16:payload_end]
    checksum = snapshot[payload_end : payload_end + 32]
    expected_checksum = hashlib.sha256(b"trnm_match_snapshot_checksum_v2\x00" + payload).digest()
    if checksum != expected_checksum:
        fail("core snapshot internal checksum mismatch")
    try:
        document = json.loads(payload)
    except Exception as error:
        fail(f"core snapshot JSON is invalid: {error}")
    if not isinstance(document, dict) or document.get("schema") != CORE_SNAPSHOT_SCHEMA:
        fail("core snapshot schema mismatch")
    if document.get("match_id") != logical_match_id:
        fail("core snapshot match identity mismatch")
    return document


def unpack_world(record: dict[str, Any], logical_match_id: str) -> dict[str, Any]:
    if set(record) != {"schema", "logical_match_id", "snapshot_base64", "snapshot_sha256"}:
        fail(f"World storage field set drift: {sorted(record)}")
    if record.get("schema") != WORLD_STORAGE_SCHEMA or record.get("logical_match_id") != logical_match_id:
        fail("World storage identity mismatch")
    snapshot = canonical_base64(record.get("snapshot_base64"), "World snapshot", 16 * 1024 * 1024)
    require_sha256(record.get("snapshot_sha256"), snapshot, "World storage")
    try:
        document = json.loads(snapshot)
    except Exception as error:
        fail(f"World snapshot JSON is invalid: {error}")
    if not isinstance(document, dict) or document.get("schema") != WORLD_SNAPSHOT_SCHEMA:
        fail("World snapshot schema mismatch")
    if set(document) != {"schema", "state", "reservations", "receipts", "retired"}:
        fail("World snapshot field set drift")
    return document


def accepted_receipts(world: dict[str, Any]) -> list[dict[str, Any]]:
    receipts = world.get("receipts")
    if not isinstance(receipts, dict):
        fail("World receipts map is invalid")
    result = []
    for command_id, receipt in receipts.items():
        if not isinstance(receipt, dict) or receipt.get("client_command_id") != command_id:
            fail("World receipt map identity is invalid")
        if receipt.get("disposition") == "accepted":
            result.append(receipt)
    return result


def verify(core: dict[str, Any], world: dict[str, Any], logical_match_id: str, expected_command_id: str | None) -> dict[str, Any]:
    state = world.get("state")
    if not isinstance(state, dict) or state.get("match_id") != logical_match_id:
        fail("World deterministic state identity is invalid")
    events = core.get("events")
    if not isinstance(events, list):
        fail("core event archive is invalid")
    core_version = core.get("version")
    world_match_version = state.get("match_version")
    next_sequence = state.get("next_global_event_sequence")
    if not isinstance(core_version, int) or core_version != len(events) + 1:
        fail("core version does not equal event count plus one")
    if world_match_version != core_version or next_sequence != len(events) + 1:
        fail("core and World authority cursors diverged")

    accepted = accepted_receipts(world)
    if not accepted:
        fail("World snapshot contains no accepted receipt")
    latest = max(accepted, key=lambda receipt: receipt.get("event_sequence") or 0)
    event_sequence = latest.get("event_sequence")
    if not isinstance(event_sequence, int) or event_sequence < 1 or event_sequence > len(events):
        fail("latest accepted receipt event sequence is invalid")
    event = events[event_sequence - 1]
    if not isinstance(event, dict):
        fail("bound core event is invalid")
    if event.get("event_type") != "agent_command_applied":
        fail("accepted World receipt is not bound to a command event")
    if event.get("causation_id") != latest.get("client_command_id"):
        fail("World receipt and core command causation differ")
    if event.get("match_version") != latest.get("match_version") or event.get("sequence") != event_sequence:
        fail("World receipt and core event cursors differ")
    if latest.get("match_version") != core_version:
        fail("latest accepted receipt does not describe current core version")
    if latest.get("state_revision") != state.get("state_revision") or latest.get("state_hash") != state.get("state_hash") or latest.get("tick") != state.get("tick"):
        fail("latest accepted receipt does not describe current deterministic state")
    if expected_command_id and latest.get("client_command_id") != expected_command_id:
        fail("latest accepted receipt is not the expected command")
    reservations = world.get("reservations")
    if not isinstance(reservations, dict) or latest.get("client_command_id") in reservations:
        fail("committed command remains pending")

    return {
        "contract_version": "trnm_game_world_command_storage_atomicity_report_v1",
        "logical_match_id": logical_match_id,
        "core": {
            "version": core_version,
            "event_count": len(events),
            "runtime_generation": None,
        },
        "world": {
            "match_version": world_match_version,
            "next_global_event_sequence": next_sequence,
            "state_revision": state.get("state_revision"),
            "state_hash": state.get("state_hash"),
            "tick": state.get("tick"),
            "accepted_receipts": len(accepted),
            "pending_reservations": len(reservations),
        },
        "latest_receipt": {
            "client_command_id": latest.get("client_command_id"),
            "event_sequence": event_sequence,
            "match_version": latest.get("match_version"),
            "request_hash": latest.get("request_hash"),
            "transition_id": latest.get("transition_id"),
            "world_outcome_hash": latest.get("world_outcome_hash"),
            "world_transition_hash": latest.get("world_transition_hash"),
        },
        "atomicity_verified": True,
        "cutover_authorized": False,
        "public_online_enabled": False,
        "public_player_market_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=pathlib.Path, required=True)
    parser.add_argument("--world", type=pathlib.Path, required=True)
    parser.add_argument("--logical-match-id", required=True)
    parser.add_argument("--expected-command-id")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    core_record = read_json(args.core, "core storage record")
    world_record = read_json(args.world, "World storage record")
    report = verify(
        unpack_core(core_record, args.logical_match_id),
        unpack_world(world_record, args.logical_match_id),
        args.logical_match_id,
        args.expected_command_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
