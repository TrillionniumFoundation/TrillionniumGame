#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.denominator.source_manifest import generate


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the fail-closed DEN-SOURCE candidate")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-sg1", action="store_true")
    args = parser.parse_args()
    value = generate(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.require_sg1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
