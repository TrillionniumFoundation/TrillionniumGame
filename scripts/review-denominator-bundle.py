#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.denominator.review_lock import (  # noqa: E402
    ReviewError,
    aggregate_reviewed_locks,
    canonical_bytes,
    load_json,
    review_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review")
    review.add_argument("--candidate", type=Path, required=True)
    review.add_argument("--review", type=Path, required=True)
    review.add_argument("--policy", type=Path, required=True)
    review.add_argument("--backlog", type=Path, required=True)
    review.add_argument("--gates", type=Path, required=True)
    review.add_argument("--previous-lock", type=Path)
    review.add_argument("--require-remote-evidence", action="store_true")
    review.add_argument("--output", type=Path, required=True)
    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--policy", type=Path, required=True)
    aggregate.add_argument("--lock", type=Path, action="append", required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy = load_json(args.policy)
        if args.command == "review":
            result = review_candidate(
                candidate_bytes=args.candidate.read_bytes(),
                review=load_json(args.review),
                policy=policy,
                backlog=load_json(args.backlog),
                gates=load_json(args.gates),
                require_remote_evidence=args.require_remote_evidence,
                previous_lock=load_json(args.previous_lock) if args.previous_lock else None,
            ).lock
        else:
            result = aggregate_reviewed_locks(
                [load_json(path) for path in args.lock], policy
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_bytes(result) + b"\n")
        print(json.dumps({"status": result["status"], "content_sha256": result["content_sha256"]}, sort_keys=True))
    except (OSError, ReviewError, json.JSONDecodeError) as exc:
        print(f"denominator review failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
