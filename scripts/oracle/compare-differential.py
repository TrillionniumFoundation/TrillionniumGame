#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.oracle.differential import canonical_json, compare_corpus
from tools.oracle.normalize import load_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--normalizers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-attempts", type=int, default=10)
    parser.add_argument(
        "--fail-on",
        default="P0,P1",
        help="comma-separated divergence severities that make the command fail",
    )
    args = parser.parse_args()
    try:
        registry = load_registry(args.normalizers)
        observations = []
        for path in sorted(args.input_dir.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"{path}: observation must be an object")
            observations.append(value)
        evidence = compare_corpus(observations, registry, args.required_attempts)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json(evidence) + b"\n")
        fail_on = {item.strip() for item in args.fail_on.split(",") if item.strip()}
        failed = {
            severity: count
            for severity, count in evidence["divergence_counts"].items()
            if severity in fail_on and count
        }
        print(
            json.dumps(
                {
                    "content_sha256": evidence["content_sha256"],
                    "divergence_counts": evidence["divergence_counts"],
                    "failed_severities": failed,
                    "claims": evidence["claims"],
                },
                sort_keys=True,
            )
        )
        return 2 if failed else 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"oracle differential comparison failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
