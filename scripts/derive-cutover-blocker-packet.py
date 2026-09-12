#!/usr/bin/env python3
"""Derive a diagnostic cutover blocker packet without promotion authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CLOSED = {"closed", "rejected", "superseded"}


def derive(gap_register: dict, binding: dict) -> dict:
    open_rows = [
        {
            "id": row.get("id"),
            "severity": row.get("severity"),
            "status": row.get("status"),
            "external_dependency": row.get("external_dependency"),
        }
        for row in gap_register.get("gaps", [])
        if row.get("severity") in {"P0", "P1"}
        and row.get("status") not in CLOSED
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
        "authority": {
            "diagnostic_only": True,
            "authenticated_issuer": False,
            "signature_verified": False,
            "expiry_and_nonce_verified": False,
            "replay_protection_verified": False,
            "may_authorize_transition": False,
        },
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
    value = derive(
        json.loads(args.gap_register.read_text(encoding="utf-8")),
        json.loads(args.binding.read_text(encoding="utf-8")),
    )
    args.output.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "open_p0_p1_count": value["open_p0_p1_count"],
                "may_authorize_transition": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
