#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def observation(lane: str, attempt: int, database_divergence: bool) -> dict[str, Any]:
    database_effects: list[dict[str, str]] = []
    if database_divergence and lane == "instrumented" and attempt == 10:
        database_effects = [{"table": "users", "operation": "unexpected-insert"}]
    return {
        "schema": "trillionnium.oracle-observation.v1",
        "lane": lane,
        "run_id": f"synthetic-{lane}-{attempt}",
        "case_id": "synthetic-account-get",
        "attempt": attempt,
        "input_sha256": "sha256:" + "a" * 64,
        "surfaces": {
            "account": {
                "surface": "account",
                "value": {
                    "user": {
                        "id": "stable-user-id",
                        "username": "stable-user",
                        "create_time": f"clock-{lane}-{attempt}",
                        "update_time": f"clock-{lane}-{attempt}",
                    }
                },
            },
            "http": {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_class": "json",
            },
            "database_effects": database_effects,
            "hooks": [],
            "provider_intents": [],
            "metrics": {"requests": 1},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=10)
    parser.add_argument("--database-divergence", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, args.attempts + 1):
        for lane in ("immutable", "instrumented"):
            path = args.output_dir / f"synthetic-{attempt:02d}-{lane}.json"
            path.write_text(
                json.dumps(
                    observation(lane, attempt, args.database_divergence),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
