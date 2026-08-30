#!/usr/bin/env python3
"""Inventory mutable GitHub Actions and container references."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
USES = re.compile(r"^\s*-?\s*uses:\s*([^#\s]+)", re.MULTILINE)
IMAGE = re.compile(r"^\s*(?:image|container):\s*([^#\s]+)", re.MULTILINE)
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def classify_use(value: str) -> str:
    if value.startswith("./"):
        return "local"
    if "@" not in value:
        return "missing-ref"
    reference = value.rsplit("@", 1)[1]
    return "immutable-sha" if SHA40.fullmatch(reference) else "mutable-ref"


def classify_image(value: str) -> str:
    return "immutable-digest" if "@sha256:" in value else "mutable-image"


def audit() -> dict[str, object]:
    references: list[dict[str, str]] = []
    workflow_paths = sorted(WORKFLOWS.glob("*.y*ml"))
    for path in workflow_paths:
        source = path.read_text(encoding="utf-8")
        for value in USES.findall(source):
            references.append(
                {
                    "workflow": str(path.relative_to(ROOT)),
                    "type": "uses",
                    "reference": value,
                    "classification": classify_use(value),
                }
            )
        for value in IMAGE.findall(source):
            references.append(
                {
                    "workflow": str(path.relative_to(ROOT)),
                    "type": "image",
                    "reference": value,
                    "classification": classify_image(value),
                }
            )
    problems = [
        row
        for row in references
        if row["classification"] in {"missing-ref", "mutable-ref", "mutable-image"}
    ]
    return {
        "schema": "trillionnium.workflow-pin-audit.v1",
        "project_id": "trillionnium-game",
        "workflow_count": len(workflow_paths),
        "reference_count": len(references),
        "problem_count": len(problems),
        "problems": problems,
        "references": references,
        "claims": {
            "source_inventory_complete": bool(workflow_paths),
            "all_references_immutable": not problems,
            "actions_enabled": False,
            "dependencies_reviewed": False,
            "supply_chain_gate_complete": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-on-mutable", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit()
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps(
        {
            "reference_count": result["reference_count"],
            "problem_count": result["problem_count"],
            "all_references_immutable": result["claims"]["all_references_immutable"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ))
    if args.fail_on_mutable and result["problem_count"]:
        for row in result["problems"]:
            print(
                f"{row['workflow']}: {row['classification']}: {row['reference']}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
