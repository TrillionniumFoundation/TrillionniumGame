#!/usr/bin/env python3
"""Compare generated CEX repository semantics without emitting source content."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} root must be an object")
    return value


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: cex-semantic-compare.py OLD NEW EXACT_COMMIT", file=sys.stderr)
        return 64
    old_path = Path(sys.argv[1])
    new_path = Path(sys.argv[2])
    commit = sys.argv[3]
    old = load(old_path)
    new = load(new_path)
    old_hash = old.pop("source_tree_sha256", None)
    new_hash = new.pop("source_tree_sha256", None)
    differing = sorted(
        key for key in set(old) | set(new) if old.get(key) != new.get(key)
    )
    report = {
        "schema": "cex.semantic-export-comparison.v1",
        "repository": "TrillionniumFoundation/CEX",
        "commit": commit,
        "old_source_tree_sha256": old_hash,
        "new_source_tree_sha256": new_hash,
        "all_other_json_equal": not differing,
        "differing_top_level_keys": differing,
        "generated_file_sha256": "sha256:"
        + hashlib.sha256(new_path.read_bytes()).hexdigest(),
        "production_authorization": "not_granted",
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if not differing else 1


if __name__ == "__main__":
    raise SystemExit(main())
