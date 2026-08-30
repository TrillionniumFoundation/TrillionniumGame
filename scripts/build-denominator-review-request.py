#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from tools.denominator.review_request import build_review_package


def main() -> int:
    parser = argparse.ArgumentParser(description="Build exact-head denominator review requests")
    parser.add_argument("--candidate", action="append", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--remote-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path("config/denominator-review-policy.json"))
    parser.add_argument("--routing", type=Path, default=Path("config/denominator-review-routing.json"))
    args = parser.parse_args()
    worklist = build_review_package(
        candidate_paths=args.candidate,
        head_sha=args.head_sha,
        remote_index_path=args.remote_index,
        output_dir=args.output_dir,
        policy_path=args.policy,
        routing_path=args.routing,
    )
    print(
        f"denominators={worklist['candidate_count']} leaves={worklist['total_leaf_count']} "
        f"manual_blockers={worklist['manual_blocker_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
