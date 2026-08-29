#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.denominator.common import DenominatorError, require_candidate_not_promoted, verify_root, write_json
from tools.denominator.console import extract_console
from tools.denominator.metrics_ops import extract_metrics_ops
from tools.denominator.provider_iap import extract_provider_iap


def generate(source: Path, output: Path) -> dict[str, object]:
    verify_root(source)
    console, console_reconciliation = extract_console(source)
    providers, iap, provider_reconciliation = extract_provider_iap(source)
    metrics, ops, ops_reconciliation = extract_metrics_ops(source)
    outputs = {
        "console-denominator.candidate.json": console,
        "console-reconciliation.candidate.json": console_reconciliation,
        "providers-denominator.candidate.json": providers,
        "iap-denominator.candidate.json": iap,
        "provider-iap-reconciliation.candidate.json": provider_reconciliation,
        "metrics-denominator.candidate.json": metrics,
        "operations-denominator.candidate.json": ops,
        "metrics-ops-reconciliation.candidate.json": ops_reconciliation,
    }
    for value in (console, providers, iap, metrics, ops):
        require_candidate_not_promoted(value)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in outputs.items():
        write_json(output / name, value)
    sums = []
    for name in sorted(outputs):
        digest = hashlib.sha256((output / name).read_bytes()).hexdigest()
        sums.append(f"{digest}  {name}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return {
        "status": "candidate-unclassified",
        "console": console["leaf_count"],
        "providers": providers["leaf_count"],
        "iap": iap["leaf_count"],
        "metrics": metrics["leaf_count"],
        "ops": ops["leaf_count"],
        "manual_contracts": sum(value["manual_contract_count"] for value in (console, providers, iap, metrics, ops)),
        "sg1_eligible": False,
        "compatibility_credit": False,
    }


def require_sg1(output: Path) -> None:
    failures = []
    for path in sorted(output.glob("*-denominator.candidate.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("status") != "reviewed-locked":
            failures.append(f"{path.name}: not reviewed-locked")
        if value.get("unclassified_count") != 0:
            failures.append(f"{path.name}: unclassified leaves remain")
        if value.get("manual_contract_count") != 0:
            failures.append(f"{path.name}: manual contracts remain")
        if value.get("sg1_eligible") is not True:
            failures.append(f"{path.name}: sg1_eligible false")
    if failures:
        raise DenominatorError("SG1 remains open: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-sg1", action="store_true")
    args = parser.parse_args()
    try:
        result = generate(args.source_dir, args.output_dir)
        if args.require_sg1:
            require_sg1(args.output_dir)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (DenominatorError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"remaining denominator generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
