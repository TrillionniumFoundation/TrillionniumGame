#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.runtime.conformance import (  # noqa: E402
    ConformanceError,
    canonical_bytes,
    compare_observations,
    evaluate_engine_selection,
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        observations = [json.loads(line) for line in args.observations.read_text(encoding="utf-8").splitlines() if line.strip()]
        evidence = compare_observations(observations, load(args.policy), load(args.corpus))
        result = evaluate_engine_selection(evidence, load(args.review), load(args.policy)) if args.review else evidence
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_bytes(result) + b"\n")
        print(json.dumps({"status": result["status"], "content_sha256": result["content_sha256"]}, sort_keys=True))
    except (OSError, json.JSONDecodeError, ConformanceError) as exc:
        print(f"runtime conformance failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
