#!/usr/bin/env python3
"""Validate contiguous, exact-candidate database endurance ledgers."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

TARGET_SECONDS = {"24h": 24 * 3600, "72h": 72 * 3600, "7d": 7 * 24 * 3600}
PROFILES = {"postgresql", "cockroachdb"}
MAX_SEGMENT_SECONDS = 21_600
MAX_SEGMENT_OVERHEAD_SECONDS = 300
MAX_SEGMENT_GAP_SECONDS = 900
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
DIGEST_PINNED_IMAGE = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")


class ValidationError(RuntimeError):
    pass


def require(value: bool, message: str) -> None:
    if not value:
        raise ValidationError(message)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_integer(value: Any, label: str) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), label)
    return value


def positive_number(value: Any, label: str) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), label)
    result = float(value)
    require(math.isfinite(result) and result > 0, label)
    return result


def exact_digest(value: Any, label: str) -> str:
    require(isinstance(value, str) and HEX64.fullmatch(value) is not None, label)
    return value


def exact_commit(value: Any, label: str) -> str:
    require(isinstance(value, str) and HEX40.fullmatch(value) is not None, label)
    return value


def load_segments(paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    values = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        require(
            value.get("schema") == "trillionnium.database-endurance-segment.v1",
            f"{path}: schema",
        )
        values.append((path, value))
    values.sort(key=lambda row: row[1].get("segment_index", -1))
    return values


def validate_capacity_manifest(value: Any, path: Path) -> dict[str, Any]:
    require(isinstance(value, dict), f"{path}: capacity manifest")
    require(
        value.get("schema") == "trillionnium.database-capacity-segment.v1",
        f"{path}: capacity schema",
    )
    require(value.get("profile") in PROFILES, f"{path}: profile")
    exact_commit(value.get("candidate_commit"), f"{path}: candidate commit")
    exact_commit(value.get("candidate_tree"), f"{path}: candidate tree")
    exact_digest(value.get("workload_sha256"), f"{path}: workload digest")
    exact_digest(
        value.get("database_logical_id_sha256"), f"{path}: database identity digest"
    )
    exact_digest(value.get("stdout_sha256"), f"{path}: stdout digest")
    exact_digest(value.get("stderr_sha256"), f"{path}: stderr digest")
    image = value.get("client_image_reference")
    require(
        isinstance(image, str) and DIGEST_PINNED_IMAGE.fullmatch(image) is not None,
        f"{path}: client image must be digest pinned",
    )
    require(
        value.get("database_endpoint_redacted") == "<redacted>",
        f"{path}: endpoint redaction",
    )

    requested = exact_integer(
        value.get("requested_duration_seconds"), f"{path}: requested duration"
    )
    observed = exact_integer(
        value.get("observed_duration_seconds"), f"{path}: observed duration"
    )
    started = exact_integer(value.get("started_epoch_seconds"), f"{path}: start time")
    finished = exact_integer(value.get("finished_epoch_seconds"), f"{path}: finish time")
    require(5 <= requested <= MAX_SEGMENT_SECONDS, f"{path}: invalid requested duration")
    require(observed >= requested, f"{path}: observed duration below requested")
    require(
        observed <= requested + MAX_SEGMENT_OVERHEAD_SECONDS,
        f"{path}: observed duration exceeds bounded overhead",
    )
    require(started > 0 and finished > started, f"{path}: invalid timestamps")
    require(finished - started == observed, f"{path}: timestamp duration mismatch")

    clients = exact_integer(value.get("clients"), f"{path}: clients")
    threads = exact_integer(value.get("threads"), f"{path}: threads")
    require(1 <= clients <= 256, f"{path}: clients out of range")
    require(1 <= threads <= clients, f"{path}: threads out of range")
    transactions = exact_integer(value.get("transactions"), f"{path}: transactions")
    failed = exact_integer(
        value.get("failed_transactions"), f"{path}: failed transactions"
    )
    require(transactions > 0, f"{path}: empty workload")
    require(failed == 0, f"{path}: failed transactions")
    positive_number(value.get("latency_average_ms"), f"{path}: latency")
    positive_number(value.get("transactions_per_second"), f"{path}: throughput")
    claims = value.get("claim_boundary")
    require(
        isinstance(claims, dict) and claims and not any(claims.values()),
        f"{path}: positive capacity claim",
    )
    return value


def validate(paths: list[Path], target: str) -> dict[str, Any]:
    require(target in TARGET_SECONDS, "unsupported target")
    values = load_segments(paths)
    require(values, "empty endurance ledger")

    first_manifest = validate_capacity_manifest(
        values[0][1].get("capacity_manifest"), values[0][0]
    )
    profile = first_manifest["profile"]
    candidate_commit = first_manifest["candidate_commit"]
    candidate_tree = first_manifest["candidate_tree"]
    workload = first_manifest["workload_sha256"]
    database_identity = first_manifest["database_logical_id_sha256"]
    client_image = first_manifest["client_image_reference"]
    clients = first_manifest["clients"]
    threads = first_manifest["threads"]

    previous_digest = "GENESIS"
    previous_finished: int | None = None
    total_requested = 0
    total_observed = 0
    total_transactions = 0
    maximum_latency = 0.0
    maximum_gap = 0
    weighted_tps_numerator = 0.0

    for expected, (path, segment) in enumerate(values):
        require(
            exact_integer(segment.get("segment_index"), f"{path}: segment index")
            == expected,
            f"segment gap at {expected}",
        )
        require(
            segment.get("previous_segment_sha256") == previous_digest,
            f"{path}: previous digest",
        )
        capacity = validate_capacity_manifest(segment.get("capacity_manifest"), path)
        require(
            segment.get("capacity_manifest_sha256")
            == hashlib.sha256(canonical(capacity)).hexdigest(),
            f"{path}: capacity manifest digest",
        )
        require(capacity["profile"] == profile, f"{path}: profile changed")
        require(
            capacity["candidate_commit"] == candidate_commit,
            f"{path}: commit changed",
        )
        require(capacity["candidate_tree"] == candidate_tree, f"{path}: tree changed")
        require(capacity["workload_sha256"] == workload, f"{path}: workload changed")
        require(
            capacity["database_logical_id_sha256"] == database_identity,
            f"{path}: database identity changed",
        )
        require(
            capacity["client_image_reference"] == client_image,
            f"{path}: client image changed",
        )
        require(capacity["clients"] == clients, f"{path}: client count changed")
        require(capacity["threads"] == threads, f"{path}: thread count changed")

        started = capacity["started_epoch_seconds"]
        finished = capacity["finished_epoch_seconds"]
        if previous_finished is not None:
            require(started >= previous_finished, f"{path}: segment time overlaps")
            gap = started - previous_finished
            require(gap <= MAX_SEGMENT_GAP_SECONDS, f"{path}: segment gap too large")
            maximum_gap = max(maximum_gap, gap)
        previous_finished = finished

        requested = capacity["requested_duration_seconds"]
        observed = capacity["observed_duration_seconds"]
        total_requested += requested
        total_observed += observed
        total_transactions += capacity["transactions"]
        latency = float(capacity["latency_average_ms"])
        maximum_latency = max(maximum_latency, latency)
        weighted_tps_numerator += (
            float(capacity["transactions_per_second"]) * observed
        )
        previous_digest = sha(path)

    required = TARGET_SECONDS[target]
    require(
        total_requested >= required,
        f"requested endurance duration {total_requested} below {required}",
    )
    require(
        total_observed >= required,
        f"observed endurance duration {total_observed} below {required}",
    )
    return {
        "schema": "trillionnium.database-endurance-ledger.v1",
        "target": target,
        "profile": profile,
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "workload_sha256": workload,
        "database_logical_id_sha256": database_identity,
        "client_image_reference": client_image,
        "clients": clients,
        "threads": threads,
        "segments": len(values),
        "requested_duration_seconds": total_requested,
        "observed_duration_seconds": total_observed,
        "first_started_epoch_seconds": first_manifest["started_epoch_seconds"],
        "last_finished_epoch_seconds": previous_finished,
        "maximum_segment_gap_seconds": maximum_gap,
        "total_transactions": total_transactions,
        "maximum_segment_average_latency_ms": maximum_latency,
        "duration_weighted_transactions_per_second": weighted_tps_numerator
        / total_observed,
        "final_segment_sha256": previous_digest,
        "claim_boundary": {
            "capacity_target_accepted": False,
            "performance_accepted": False,
            "independently_accepted": False,
            "production_ready": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGET_SECONDS))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("segments", nargs="+", type=Path)
    args = parser.parse_args()
    report = validate(args.segments, args.target)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
