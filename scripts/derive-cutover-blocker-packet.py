#!/usr/bin/env python3
"""Derive the current cutover blocker packet without promoting any claim."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CLOSED = {"closed", "rejected", "superseded"}

def derive(gap_register: dict, binding: dict) -> dict:
    open_rows = [
        {"id": row.get("id"), "severity": row.get("severity"), "status": row.get("status"), "external_dependency": row.get("external_dependency")}
        for row in gap_register.get("gaps", [])
        if row.get("severity") in {"P0", "P1"} and row.get("status") not in CLOSED
    ]
    open_rows.sort(key=lambda row: (row.get("severity") or "", row.get("id") or ""))
    return {
        "schema": "trillionnium.cutover-blocker-packet.v1",
        "candidate_binding": binding,
        "open_p0_p1_count": len(open_rows),
        "open_p0_p1": open_rows,
        "all_required_evidence_accepted": False,
        "independent_review_complete": False,
        "governance_readback_complete": False,
        "claim_boundary": {
            "shadow_authorized": False,
            "exclusive_canary_authorized": False,
            "production_authorized": False,
            "retirement_authorized": False,
        },
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gap-register", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = derive(json.loads(args.gap_register.read_text()), json.loads(args.binding.read_text()))
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"open_p0_p1_count": value["open_p0_p1_count"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
