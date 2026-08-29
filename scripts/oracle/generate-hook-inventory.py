#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.oracle.hook_inventory import (  # noqa: E402
    InventoryError,
    canonical_bytes,
    generate_inventory,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        inventory = generate_inventory(args.source.resolve(), policy)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_bytes(inventory) + b"\n")
        print(json.dumps({"status": inventory["status"], "site_count": inventory["site_count"], "content_sha256": inventory["content_sha256"]}, sort_keys=True))
    except (OSError, json.JSONDecodeError, InventoryError) as exc:
        print(f"hook inventory failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
