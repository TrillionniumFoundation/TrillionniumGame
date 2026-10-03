#!/usr/bin/env python3
"""Reproduce and verify the complete denominator review packet family."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/generate-denominator-review-packets.py"
PACKETS = ROOT / "docs/review/denominator-family-packets"

class ValidationError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def load_generator():
    spec = importlib.util.spec_from_file_location("denominator_packet_generator", GENERATOR)
    require(spec is not None and spec.loader is not None, "generator loader unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def validate() -> dict:
    generator = load_generator()
    with tempfile.TemporaryDirectory() as temporary:
        generated = Path(temporary) / "packets"
        expected_index = generator.generate(generated)
        tracked = sorted(path.relative_to(PACKETS) for path in PACKETS.rglob("*") if path.is_file())
        reproduced = sorted(path.relative_to(generated) for path in generated.rglob("*") if path.is_file())
        require(tracked == reproduced, "packet file inventory drift")
        for relative in tracked:
            require((PACKETS / relative).read_bytes() == (generated / relative).read_bytes(), f"packet drift: {relative}")
    index = json.loads((PACKETS / "index.json").read_text(encoding="utf-8"))
    require(index == expected_index, "index differs from reproduced authority")
    require(index.get("family_count") == 14, "family count")
    require(index.get("leaf_count") == 10173, "leaf count")
    for row in index.get("families", []):
        packet = json.loads((PACKETS / row["packet"]).read_text(encoding="utf-8"))
        require(packet.get("leaf_count") == len(packet.get("leaves", [])), f"{row['family_id']}: leaf count")
        require(all(leaf.get("classification") is None for leaf in packet["leaves"]), f"{row['family_id']}: synthesized classification")
        require(not any(packet.get("claim_boundary", {}).values()), f"{row['family_id']}: positive claim")
    require(not any(index.get("claim_boundary", {}).values()), "positive global claim")
    return index

def main() -> int:
    try:
        index = validate()
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"denominator review packet validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"families": index["family_count"], "leaves": index["leaf_count"], "status": "reproducible-unclassified"}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
