#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.upstream.archive_repair import repair_profile
from tools.upstream.pinned_archive import SourceArchiveError


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair explicitly pinned GitHub source-archive transport differences, "
            "then require the canonical Git root tree"
        )
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-blob-bytes", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args()
    try:
        result = repair_profile(
            registry=args.registry,
            profile_id=args.profile,
            root=args.root,
            token=os.environ.get(args.token_env),
            timeout_seconds=args.timeout_seconds,
            max_blob_bytes=args.max_blob_bytes,
        )
    except (SourceArchiveError, OSError) as error:
        print(f"pinned archive repair failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
