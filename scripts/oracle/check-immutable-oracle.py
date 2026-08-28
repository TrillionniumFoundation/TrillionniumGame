#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.oracle.normalize import load_registry  # noqa: E402

NAKAMA_IMAGE = "heroiclabs/nakama:3.40.0@sha256:92fb184e3271be12fd4d239766afb285322a50aaf769a59433445d59624c78cd"
POSTGRES_IMAGE = "postgres:17.6-alpine3.22@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94"


class CheckError(RuntimeError):
    pass


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckError(f"{path}: root must be an object")
    return value


def check(root: Path) -> None:
    lock_path = root / "oracle/immutable/oracle-lock.json"
    compose_path = root / "oracle/immutable/compose.yml"
    registry_path = root / "config/oracle-normalizers.json"
    renderer_path = root / "scripts/oracle/render-immutable-evidence.py"
    runner_path = root / "scripts/oracle/run-immutable-smoke.sh"
    for path in (lock_path, compose_path, registry_path, renderer_path, runner_path):
        if not path.is_file():
            raise CheckError(f"missing {path.relative_to(root)}")

    lock = load(lock_path)
    compose = compose_path.read_text(encoding="utf-8")
    try:
        registry = load_registry(registry_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CheckError(f"normalizer registry rejected: {exc}") from exc
    if lock.get("schema") != "trillionnium.immutable-oracle-lock.v2":
        raise CheckError("unexpected oracle lock schema")
    if lock.get("oracle_lane") != "immutable":
        raise CheckError("oracle lane must be immutable")
    if lock.get("normalizer_registry") != "config/oracle-normalizers.json":
        raise CheckError("oracle lock is not bound to the canonical normalizer registry")
    if lock.get("nakama", {}).get("image") != NAKAMA_IMAGE:
        raise CheckError("Nakama image is not the reviewed digest")
    if lock.get("database", {}).get("image") != POSTGRES_IMAGE:
        raise CheckError("PostgreSQL image is not the reviewed digest")
    claims = lock.get("claims", {})
    if not claims or any(claims.values()):
        raise CheckError("bootstrap claims must all remain false")

    for image in (NAKAMA_IMAGE, POSTGRES_IMAGE):
        if compose.count(image) != 1:
            raise CheckError(f"compose must contain exact image once: {image}")
    for marker in (":latest", "../", "/home/", "Trillionnium-Nakama", "privileged: true"):
        if marker in compose:
            raise CheckError(f"forbidden compose marker: {marker}")
    if not re.search(r"127\.0\.0\.1:\$\{TRNM_ORACLE_HTTP_PORT:-0\}:7350", compose):
        raise CheckError("Nakama client port is not loopback-bound")
    postgres_section = compose.split("  nakama:", 1)[0]
    if "ports:" in postgres_section:
        raise CheckError("PostgreSQL must not publish a host port")
    if "internal: true" not in compose:
        raise CheckError("backend network must be internal")
    if lock.get("network_policy", {}).get("shared_writable_database_with_other_lane") is not False:
        raise CheckError("immutable lane may not share a writable database")

    required = set(lock.get("required_evidence", []))
    for field in ("candidate_commit", "normalizer_registry_sha256", "rendered_config_sha256"):
        if field not in required:
            raise CheckError(f"oracle evidence contract does not require {field}")
    if len(registry["allowed"]) != 6 or registry.get("status") != "candidate-reviewed-required":
        raise CheckError("unexpected normalizer registry candidate state")

    combined = lock_path.read_text(encoding="utf-8") + renderer_path.read_text(encoding="utf-8")
    for claim in ("sg2_complete", "compatibility_credit", "production_ready", "public_online"):
        if claim not in combined:
            raise CheckError(f"missing explicit claim field: {claim}")
    for script in (renderer_path, runner_path):
        if "nakama_retired=true" in script.read_text(encoding="utf-8").lower():
            raise CheckError("oracle scripts overclaim Nakama retirement")

    print(json.dumps({
        "status": "static-oracle-contract-passed",
        "lock_sha256": "sha256:" + hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "normalizer_registry_sha256": "sha256:" + hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "allowed_normalizers": len(registry["allowed"]),
        "sg2_complete": False,
        "compatibility_credit": False,
    }, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except CheckError as exc:
        print(f"immutable oracle contract failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
