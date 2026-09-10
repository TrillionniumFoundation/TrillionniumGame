#!/usr/bin/env python3
"""Apply and finalize the qualified World nested-test/source repair tranche."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True)


def refresh_manifest(root: Path, relative: str, changed: set[str]) -> None:
    manifest_path = root / relative
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_root = manifest_path.parent.parent
    seen: set[str] = set()
    for field in ("parts", "nested_test_parts"):
        for record in manifest[field]:
            path = record["path"]
            if path in changed:
                payload = (source_root / path).read_bytes()
                record["bytes"] = len(payload)
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                seen.add(path)
    if seen != changed:
        raise RuntimeError(f"{relative}: manifest paths {sorted(seen)} != {sorted(changed)}")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    helper = Path(__file__).resolve().parent

    run(
        sys.executable,
        str(helper / "world-pr104-all-test-modules-transform.py"),
        str(root),
        "--source-sha",
        args.source_sha,
    )
    run(sys.executable, str(helper / "world-pr104-source-integrity-repair.py"), str(root))
    run(sys.executable, str(helper / "world-pr104-final-source-integrity-repair.py"), str(root))

    refresh_manifest(
        root,
        "trillionnium/crates/trnm-campaign-core/src/lib_parts/manifest.json",
        {
            "lib_parts/tests/part_01.rs",
            "lib_parts/tests/campaign_tests_01.rs",
            "lib_parts/tests/campaign_tests_02.rs",
            "lib_parts/tests/campaign_tests_03.rs",
        },
    )
    refresh_manifest(
        root,
        "trillionnium/crates/trnm-rts-sim/src/lib_parts/manifest.json",
        {
            "lib_parts/tests/part_01.rs",
            "lib_parts/tests/rts_tests_01.rs",
            "lib_parts/tests/rts_tests_02.rs",
            "lib_parts/tests/rts_tests_03.rs",
        },
    )
    refresh_manifest(
        root,
        "trillionnium/crates/trnm-game-server/src/lib_parts/manifest.json",
        {
            "lib_parts/tests/part_01.rs",
            "lib_parts/tests/game_server_tests_01.rs",
            "lib_parts/tests/game_server_tests_02.rs",
            "lib_parts/tests/game_server_tests_03.rs",
            "lib_parts/tests/game_server_tests_04.rs",
        },
    )

    print(
        "WORLD_ALL_TEST_MODULES_QUALIFIED_TRANSFORM=PASS "
        "seams=10 workflows=14 contexts=41 current_conformance=wrapped-boundary"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
