#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

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
    renderer_path = root / "scripts/oracle/render-immutable-evidence.py"
    runner_path = root / "scripts/oracle/run-immutable-smoke.sh"
    for path in (lock_path, compose_path, renderer_path, runner_path):
        if not path.is_file():
            raise CheckError(f"missing {path.relative_to(root)}")

    lock = load(lock_path)
    compose = compose_path.read_text(encoding="utf-8")
    if lock.get("schema") != "trillionnium.immutable-oracle-lock.v1":
        raise CheckError("unexpected oracle lock schema")
    if lock.get("oracle_lane") != "immutable":
        raise CheckError("oracle lane must be immutable")
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
    forbidden = [":latest", "../", "/home/", "Trillionnium-Nakama", "privileged: true"]
    for marker in forbidden:
        if marker in compose:
            raise CheckError(f"forbidden compose marker: {marker}")
    if not re.search(r"127\.0\.0\.1:\$\{TRNM_ORACLE_HTTP_PORT:-0\}:7350", compose):
        raise CheckError("Nakama client port is not loopback-bound")
    postgres_section = compose.split("  nakama:", 1)[0]
    if "ports:" in postgres_section:
        raise CheckError("PostgreSQL must not publish a host port")
    if "internal: true" not in compose:
        raise CheckError("backend network must be internal")
    if "shared_writable_database_with_other_lane" not in lock.get("network_policy", {}):
        raise CheckError("oracle lock does not state shared database policy")
    if lock["network_policy"]["shared_writable_database_with_other_lane"] is not False:
        raise CheckError("immutable lane may not share a writable database")

    contract_text = lock_path.read_text(encoding="utf-8") + renderer_path.read_text(encoding="utf-8")
    for claim in ("sg2_complete", "compatibility_credit", "production_ready", "public_online"):
        if claim not in contract_text:
            raise CheckError(f"missing explicit claim field: {claim}")
    for script in (renderer_path, runner_path):
        if "nakama_retired=true" in script.read_text(encoding="utf-8").lower():
            raise CheckError("oracle scripts overclaim Nakama retirement")

    digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    print(json.dumps({"status": "static-oracle-contract-passed", "lock_sha256": "sha256:" + digest}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except CheckError as exc:
        print(f"immutable oracle contract failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
