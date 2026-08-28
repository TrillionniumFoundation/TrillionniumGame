#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


class EvidenceError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path}: root must be an object")
    return value


def require_string(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise EvidenceError(f"runtime facts missing non-empty {field}")
    return item


def render(lock_path: Path, compose_path: Path, facts_path: Path) -> dict[str, Any]:
    lock = load_object(lock_path)
    facts = load_object(facts_path)
    claims = lock.get("claims")
    if not isinstance(claims, dict) or any(claims.values()):
        raise EvidenceError("oracle bootstrap lock must not grant compatibility or production claims")

    required = lock.get("required_evidence")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise EvidenceError("oracle lock has invalid required_evidence")
    for field in required:
        require_string(facts, field)

    if facts["health_status"] != "healthy":
        raise EvidenceError("immutable oracle is not healthy")
    try:
        table_count = int(facts["database_table_count"])
    except (TypeError, ValueError) as exc:
        raise EvidenceError("database_table_count is not an integer") from exc
    if table_count < 1:
        raise EvidenceError("Nakama migration produced no public database tables")

    lock_bytes = lock_path.read_bytes()
    compose_bytes = compose_path.read_bytes()
    expected_lock_hash = sha256(lock_bytes)
    expected_compose_hash = sha256(compose_bytes)
    if facts["oracle_lock_sha256"] != expected_lock_hash:
        raise EvidenceError("oracle lock digest mismatch")
    if facts["compose_sha256"] != expected_compose_hash:
        raise EvidenceError("compose digest mismatch")

    evidence = {
        "schema": "trillionnium.immutable-oracle-evidence.v1",
        "project_id": "trillionnium-game",
        "status": "immutable-oracle-smoke-passed",
        "credit": "diagnostic-only",
        "candidate": {"commit": facts["candidate_commit"]},
        "oracle": {
            "lane": "immutable",
            "lock_sha256": expected_lock_hash,
            "compose_sha256": expected_compose_hash,
            "rendered_config_sha256": facts["rendered_config_sha256"],
            "nakama": lock["nakama"],
            "nakama_common": lock["nakama_common"],
            "database": lock["database"],
            "nakama_image_id": facts["nakama_image_id"],
            "postgres_image_id": facts["postgres_image_id"],
        },
        "environment": {
            "container_runtime": facts["container_runtime"],
            "kernel": facts["kernel"],
            "architecture": facts["architecture"],
        },
        "result": {
            "health_status": facts["health_status"],
            "database_table_count": table_count,
            "started_at_utc": facts["started_at_utc"],
            "completed_at_utc": facts["completed_at_utc"],
        },
        "limitations": [
            "No instrumented oracle exists in this slice.",
            "Immutable/instrumented equivalence is not proven.",
            "No API, realtime, database-effect, provider or runtime differential is executed.",
            "The result cannot close SG2 or earn compatibility, production or public-online credit.",
        ],
        "claims": claims,
    }
    evidence["content_sha256"] = sha256(canonical(evidence))
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = render(args.lock, args.compose, args.facts)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(evidence) + b"\n")
        print(json.dumps({"status": evidence["status"], "content_sha256": evidence["content_sha256"]}, sort_keys=True))
    except EvidenceError as exc:
        print(f"immutable oracle evidence failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
