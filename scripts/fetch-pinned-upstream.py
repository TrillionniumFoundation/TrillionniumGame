#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.upstream.pinned_archive import SourceArchiveError, fetch_pinned_github_source


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a GitHub archive and verify its exact Git tree SHA")
    parser.add_argument("--repository", required=True, help="owner/name")
    parser.add_argument("--revision", required=True, help="exact non-zero 40-character commit SHA")
    parser.add_argument("--tree", required=True, help="exact non-zero 40-character root tree SHA")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-archive-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    args = parser.parse_args()
    try:
        evidence = fetch_pinned_github_source(
            repository=args.repository,
            revision=args.revision,
            tree=args.tree,
            output_dir=args.output,
            token=os.environ.get(args.token_env),
            timeout_seconds=args.timeout_seconds,
            max_archive_bytes=args.max_archive_bytes,
        )
    except (SourceArchiveError, OSError) as exc:
        print(f"pinned source fetch failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
