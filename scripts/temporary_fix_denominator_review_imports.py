#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


def patch(path_text: str) -> None:
    path = Path(path_text)
    source = path.read_text(encoding="utf-8")
    old = "import argparse\n"
    new = (
        "import argparse\n"
        "import sys\n"
        "\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "sys.path.insert(0, str(ROOT))\n"
    )
    if source.count(old) != 1:
        raise SystemExit(f"{path_text}: import anchor drift")
    source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")


def main() -> None:
    patch("scripts/generate-source-denominator.py")
    patch("scripts/build-denominator-review-request.py")


if __name__ == "__main__":
    main()
