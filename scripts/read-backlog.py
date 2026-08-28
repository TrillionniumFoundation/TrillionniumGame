#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "docs/development/EXECUTION_BACKLOG.json"


def load() -> tuple[dict, dict]:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    artifact = ROOT / index["full_backlog_artifact"]["path"]
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        backlog = json.load(handle)
    return index, backlog


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the audited TrillionniumGame execution backlog")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", action="store_true")
    group.add_argument("--workstream")
    group.add_argument("--task")
    args = parser.parse_args()

    index, backlog = load()
    if args.task:
        for workstream in backlog["workstreams"]:
            for task in workstream["tasks"]:
                if task["id"] == args.task:
                    print(json.dumps(task, ensure_ascii=False, indent=2))
                    return 0
        raise SystemExit(f"unknown task: {args.task}")
    if args.workstream:
        for workstream in backlog["workstreams"]:
            if workstream["id"] == args.workstream:
                print(json.dumps(workstream, ensure_ascii=False, indent=2))
                return 0
        raise SystemExit(f"unknown workstream: {args.workstream}")

    tasks = [task for workstream in backlog["workstreams"] for task in workstream["tasks"]]
    summary = {
        "schema": backlog["schema"],
        "plan_version": backlog["plan_version"],
        "workstreams": len(backlog["workstreams"]),
        "tasks": len(tasks),
        "priorities": {priority: sum(task["priority"] == priority for task in tasks) for priority in ("P0", "P1", "P2")},
        "estimate_person_weeks": {
            "min": sum(task["estimate_person_weeks"]["min"] for task in tasks),
            "max": sum(task["estimate_person_weeks"]["max"] for task in tasks)
        },
        "artifact_sha256": index["full_backlog_artifact"]["sha256"]
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
