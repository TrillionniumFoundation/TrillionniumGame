#!/usr/bin/env python3
from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "contracts/canonical/framing-vectors.json"
MAGIC = b"TRNMCAN1"


def encode_value(value: object) -> bytes:
    if value is None:
        return b"\x00"
    if isinstance(value, dict) and set(value) == {"bool"}:
        return b"\x02" if value["bool"] is True else b"\x01"
    if isinstance(value, dict) and set(value) == {"i64"}:
        number = value["i64"]
        if not isinstance(number, int) or isinstance(number, bool):
            raise ValueError("i64 required")
        return b"\x03" + struct.pack(">q", number)
    if isinstance(value, dict) and set(value) == {"string"}:
        data = value["string"].encode("utf-8")
        return b"\x04" + struct.pack(">I", len(data)) + data
    if isinstance(value, dict) and set(value) == {"bytes_hex"}:
        data = bytes.fromhex(value["bytes_hex"])
        return b"\x05" + struct.pack(">I", len(data)) + data
    if isinstance(value, dict) and set(value) == {"array"}:
        items = value["array"]
        return b"\x06" + struct.pack(">I", len(items)) + b"".join(
            encode_value(item) for item in items
        )
    if isinstance(value, dict) and set(value) == {"object"}:
        entries = sorted(value["object"], key=lambda item: item[0].encode("utf-8"))
        keys = [entry[0] for entry in entries]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_canonical_object_key")
        payload = bytearray(b"\x07" + struct.pack(">I", len(entries)))
        for key, item in entries:
            key_bytes = key.encode("utf-8")
            payload += struct.pack(">I", len(key_bytes)) + key_bytes + encode_value(item)
        return bytes(payload)
    raise ValueError("unsupported canonical value")


def encode_frame(case: dict[str, object]) -> bytes:
    domain = case["domain"].encode("ascii")
    version = case["version"]
    return (
        MAGIC
        + struct.pack(">H", len(domain))
        + domain
        + struct.pack(">HH", version["major"], version["minor"])
        + encode_value(case["value"])
    )


def main() -> None:
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert vectors["schema"] == "trillionnium.canonical-framing-v1"
    assert vectors["magic_ascii"] == MAGIC.decode("ascii")
    assert not any(vectors["claims"].values())
    for case in vectors["accepted"]:
        encoded = encode_frame(case)
        if expected := case.get("expected_hex"):
            assert encoded.hex() == expected, case["id"]
    print(
        json.dumps(
            {
                "status": "canonical-reference-vectors-passed",
                "accepted": len(vectors["accepted"]),
                "rejected": len(vectors["rejected"]),
                "compatibility_credit": False
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
