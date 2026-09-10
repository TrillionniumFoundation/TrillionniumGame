#!/usr/bin/env python3
"""Repair the remaining option/positional CLI drift in World CI integrity."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)

    path = root / "scripts/check-trnm-world-ci-integrity.py"
    source = path.read_text(encoding="utf-8", errors="strict")
    old = '("scripts/check-trnm-world-detailed-documentation.py", [str(root)]),'
    new = '("scripts/check-trnm-world-detailed-documentation.py", ["--root", str(root)]),'
    if source.count(old) != 1:
        raise RuntimeError(
            f"CI-integrity detailed-documentation invocation drift: {source.count(old)}"
        )
    path.write_text(source.replace(old, new), encoding="utf-8")

    aggregate = root / "scripts/check-trnm-world-documentation.py"
    aggregate_source = aggregate.read_text(encoding="utf-8", errors="strict")
    required = '("scripts/check-trnm-world-detailed-documentation.py", ["--root", str(ROOT)]),'
    if aggregate_source.count(required) != 1:
        raise RuntimeError("aggregate documentation checker is not option-root aligned")

    print("WORLD_FINAL_SOURCE_INTEGRITY_REPAIR=PASS detailed_documentation_cli=option-root")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
