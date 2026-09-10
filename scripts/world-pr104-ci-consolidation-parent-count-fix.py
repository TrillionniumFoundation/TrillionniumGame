#!/usr/bin/env python3
"""Harden the generated CI checker to require merge-parent checks in every V6 lane."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    path = args.root.resolve() / "scripts/check-trnm-world-ci-integrity.py"
    source = path.read_text(encoding="utf-8")
    old = '''    for marker in (
        "HEAD_SHA:",
        "BASE_SHA:",
        "MERGE_SHA:",
        'rev-parse HEAD^1)" = "$BASE_SHA"',
        'rev-parse HEAD^2)" = "$HEAD_SHA"',
        "run-trnm-world-v6-qualification-v2.sh",
    ):
        if marker not in admission:
            fail(f"canonical V6 admission is missing exact-object marker: {marker}")
    if admission.count("run-trnm-world-v6-qualification-v2.sh") < 8:
        fail("canonical V6 admission does not qualify truth/source/postgres/package on both objects")
'''
    new = '''    for marker in (
        "HEAD_SHA:",
        "BASE_SHA:",
        "MERGE_SHA:",
        "run-trnm-world-v6-qualification-v2.sh",
    ):
        if marker not in admission:
            fail(f"canonical V6 admission is missing exact-object marker: {marker}")
    for marker, expected_count in (
        ('test "$(git rev-parse HEAD^1)" = "$BASE_SHA"', 4),
        ('test "$(git rev-parse HEAD^2)" = "$HEAD_SHA"', 4),
    ):
        observed_count = admission.count(marker)
        if observed_count != expected_count:
            fail(
                "canonical V6 admission merge-parent coverage drift: "
                f"marker={marker!r}, expected={expected_count}, observed={observed_count}"
            )
    if admission.count("run-trnm-world-v6-qualification-v2.sh") < 8:
        fail("canonical V6 admission does not qualify truth/source/postgres/package on both objects")
'''
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"parent-count checker anchor drift: {count}")
    path.write_text(source.replace(old, new), encoding="utf-8")
    print("WORLD_CI_PARENT_COUNT_FIX=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
