#!/usr/bin/env python3
"""Validate that a ready PR binds its exact head/tree, gaps and claim boundary."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def line_value(body: str, label: str) -> str | None:
    pattern = re.compile(rf"(?im)^\s*-?\s*{re.escape(label)}\s*:\s*`?([^`\n]+)`?\s*$")
    match = pattern.search(body)
    return match.group(1).strip() if match else None


def validate_ready(body: str, head: str, tree: str) -> dict[str, object]:
    require(len(body.strip()) >= 600, "ready PR body is too short for the execution contract")
    require("TrillionniumFoundation/TrillionniumGame" in body, "canonical repository identity missing")
    require("plan version" in body.lower() or "plan v3" in body.lower(), "plan-v3 linkage missing")
    require(re.search(r"GAP-P[0-2]-[A-Z0-9-]+", body) is not None, "gap ID missing")
    require("candidate manifest" in body.lower(), "candidate manifest artifact/digest missing")
    require("independent review" in body.lower(), "independent-review section missing")
    require("claim boundary" in body.lower(), "claim boundary section missing")

    for placeholder in (
        "<40-char-sha>",
        "<64-hex>",
        "<artifact-id>",
        "TODO",
        "TBD",
    ):
        require(placeholder not in body, f"ready PR retains placeholder {placeholder!r}")

    declared_head = line_value(body, "head commit") or line_value(body, "head")
    declared_tree = line_value(body, "head tree") or line_value(body, "tree")
    require(declared_head == head, f"PR body head {declared_head!r} does not match exact head {head}")
    require(declared_tree == tree, f"PR body tree {declared_tree!r} does not match exact tree {tree}")

    lower = body.lower()
    for phrase in (
        "production-ready = false",
        "public-online = false",
        "drop-in replacement = false",
        "nakama retired = false",
    ):
        require(phrase in lower, f"fail-closed claim missing: {phrase}")

    require("empty" in lower and "skipped" in lower and "cancelled" in lower, "empty/skipped/cancelled result policy missing")
    require("self-merge" in lower or "self merge" in lower, "self-merge prohibition missing")
    return {
        "exact_head_declared": True,
        "exact_tree_declared": True,
        "gap_linked": True,
        "claim_boundary_present": True,
        "ready_contract_complete": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--draft", choices=("true", "false"), required=True)
    arguments = parser.parse_args()
    try:
        require(re.fullmatch(r"[a-f0-9]{40}", arguments.head) is not None, "invalid exact head")
        require(re.fullmatch(r"[a-f0-9]{40}", arguments.tree) is not None, "invalid exact tree")
        body = Path(arguments.body_file).read_text(encoding="utf-8")
        if arguments.draft == "true":
            result = {
                "exact_head_declared": False,
                "exact_tree_declared": False,
                "gap_linked": re.search(r"GAP-P[0-2]-[A-Z0-9-]+", body) is not None,
                "claim_boundary_present": "claim boundary" in body.lower(),
                "ready_contract_complete": False,
            }
            status = "draft-incomplete-no-merge-credit"
        else:
            result = validate_ready(body, arguments.head, arguments.tree)
            status = "ready-contract-passed"
        print(
            json.dumps(
                {
                    "schema": "trillionnium.pull-request-contract-check.v1",
                    "status": status,
                    "draft": arguments.draft == "true",
                    "head": arguments.head,
                    "tree": arguments.tree,
                    "assertions": result,
                    "claims": {
                        "merge_ready": status == "ready-contract-passed",
                        "compatibility_credit": False,
                        "production_ready": False,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, ContractError) as error:
        print(f"pull-request contract failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
