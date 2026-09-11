#!/usr/bin/env python3
"""Validate contiguous, exact-candidate database endurance ledgers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

TARGET_SECONDS = {"24h": 24 * 3600, "72h": 72 * 3600, "7d": 7 * 24 * 3600}

class ValidationError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load_segments(paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    values=[]
    for path in paths:
        value=json.loads(path.read_text(encoding="utf-8"))
        require(value.get("schema")=="trillionnium.database-endurance-segment.v1",f"{path}: schema")
        values.append((path,value))
    values.sort(key=lambda row: row[1].get("segment_index",-1))
    return values

def validate(paths: list[Path], target: str) -> dict[str, Any]:
    require(target in TARGET_SECONDS, "unsupported target")
    values=load_segments(paths)
    require(values, "empty endurance ledger")
    first=values[0][1]
    profile=first.get("profile")
    candidate_commit=first.get("candidate_commit")
    candidate_tree=first.get("candidate_tree")
    workload=first.get("workload_sha256")
    previous="GENESIS"
    total_seconds=0
    total_transactions=0
    max_latency=0.0
    weighted_tps_numerator=0.0
    for expected,(path,value) in enumerate(values):
        require(value.get("segment_index")==expected,f"segment gap at {expected}")
        require(value.get("previous_segment_sha256")==previous,f"{path}: previous digest")
        require(value.get("profile")==profile,f"{path}: profile changed")
        require(value.get("candidate_commit")==candidate_commit,f"{path}: commit changed")
        require(value.get("candidate_tree")==candidate_tree,f"{path}: tree changed")
        require(value.get("workload_sha256")==workload,f"{path}: workload changed")
        require(value.get("failed_transactions")==0,f"{path}: failed transactions")
        duration=int(value.get("observed_duration_seconds",0))
        require(5 <= duration <= 21660,f"{path}: invalid duration")
        total_seconds += duration
        tx=int(value.get("transactions",0)); require(tx>0,f"{path}: empty workload")
        total_transactions += tx
        latency=float(value.get("latency_average_ms",0)); require(latency>0,f"{path}: latency")
        max_latency=max(max_latency,latency)
        weighted_tps_numerator += float(value.get("transactions_per_second",0))*duration
        previous=sha(path)
    require(total_seconds >= TARGET_SECONDS[target],f"endurance duration {total_seconds} below {TARGET_SECONDS[target]}")
    return {
        "schema":"trillionnium.database-endurance-ledger.v1",
        "target":target,
        "profile":profile,
        "candidate_commit":candidate_commit,
        "candidate_tree":candidate_tree,
        "workload_sha256":workload,
        "segments":len(values),
        "observed_duration_seconds":total_seconds,
        "total_transactions":total_transactions,
        "maximum_segment_average_latency_ms":max_latency,
        "duration_weighted_transactions_per_second":weighted_tps_numerator/total_seconds,
        "final_segment_sha256":previous,
        "claim_boundary":{
            "capacity_target_accepted":False,
            "performance_accepted":False,
            "independently_accepted":False,
            "production_ready":False,
        },
    }

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--target",required=True,choices=sorted(TARGET_SECONDS))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("segments",nargs="+",type=Path)
    args=parser.parse_args()
    report=validate(args.segments,args.target)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
