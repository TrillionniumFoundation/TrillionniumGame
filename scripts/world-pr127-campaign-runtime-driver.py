#!/usr/bin/env python3
"""Invoke the reviewed Campaign runtime transform through its missing entrypoint.

The pinned helper defines ``main`` but does not call it when executed as a
script. This adapter invokes that exact function with an explicit argv and
fails closed if the helper moves, returns a non-zero status, or does not remove
all ten Campaign root-runtime seams.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--stack-helper", type=Path, required=True)
    parser.add_argument("--completed-commit", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if re.fullmatch(r"[0-9a-f]{40}", args.completed_commit) is None:
        raise RuntimeError("completed commit must be 40 lowercase hexadecimal characters")
    root = args.root.resolve(strict=True)
    stack = args.stack_helper.resolve(strict=True)
    helper = stack / "scripts/world-pr104-campaign-runtime-modules-transform.py"
    if not helper.is_file() or helper.is_symlink():
        raise RuntimeError(f"Campaign helper is unavailable: {helper}")

    spec = importlib.util.spec_from_file_location(
        "world_pr104_campaign_runtime_modules_transform",
        helper,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Campaign helper: {helper}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise RuntimeError("Campaign helper no longer exposes callable main")

    original_argv = sys.argv
    try:
        sys.argv = [
            str(helper),
            str(root),
            "--completed-commit",
            args.completed_commit,
        ]
        status = entrypoint()
    finally:
        sys.argv = original_argv
    if status not in (None, 0):
        raise RuntimeError(f"Campaign helper returned non-zero status: {status}")

    source = (
        root
        / "trillionnium/crates/trnm-campaign-core/src/lib.rs"
    ).read_text(encoding="utf-8", errors="strict")
    forbidden = (
        "lib_parts/contracts_and_domain/part_01.rs",
        "lib_parts/authored_content/part_01.rs",
        "lib_parts/campaign_state/part_01.rs",
        "lib_parts/campaign_commands/part_01.rs",
        "lib_parts/campaign_commands/part_02.rs",
        "lib_parts/campaign_commands/part_03.rs",
        "lib_parts/campaign_commands/part_04.rs",
        "lib_parts/campaign_commands/part_05.rs",
        "lib_parts/campaign_commands/part_06.rs",
        "lib_parts/economy_commands/part_01.rs",
    )
    remaining = [path for path in forbidden if f'include!("{path}")' in source]
    if remaining:
        raise RuntimeError(f"Campaign helper left active include seams: {remaining}")
    print("WORLD_PR127_CAMPAIGN_ENTRYPOINT_ADAPTER=PASS seams=10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
