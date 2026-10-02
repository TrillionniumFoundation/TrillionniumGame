#!/usr/bin/env python3
"""Check actual Rust schema identity against the complete current source lock.

This validates retained source/runtime identity, not migration compatibility or
constraint/index semantic equivalence. Serve verification never requires the
original schema provenance to equal a later binary's commit.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALGORITHM = "ordered-path-git-blob-sha256.v1"
MAX_IDENTITY_BYTES = 16 * 1024


class ValidationError(RuntimeError):
    """An identity is missing, stale or inconsistent with its execution mode."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValidationError(reason)


def decode_identity_document(data: bytes) -> Any:
    require(len(data) <= MAX_IDENTITY_BYTES, "schema identity exceeds byte budget")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            require(key not in value, "duplicate schema identity JSON field")
            value[key] = item
        return value

    return json.loads(data, object_pairs_hook=unique_object)


def validated_source() -> tuple[dict[str, Any], int, int]:
    path = ROOT / "scripts/check-migration-lock.py"
    spec = importlib.util.spec_from_file_location("trnm_migration_lock", path)
    require(spec is not None and spec.loader is not None, "migration validator unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.validate()
    lock = json.loads((ROOT / "migrations/MIGRATION_CHAIN.lock.json").read_text())
    authority = json.loads((ROOT / "docs/development/SCHEMA_AUTHORITY.json").read_text())
    return report["profiles"], lock["schema_version"], len(authority["adapter_abi"]["required_tables"])


def validate_identity(
    value: Any,
    *,
    profile: str,
    chains: dict[str, Any],
    schema_version: int,
    table_count: int,
    mode: str = "any",
    source_commit: str | None = None,
    from_version: int | None = None,
    v2_apply_source_commit: str | None = None,
) -> None:
    require(mode in {"any", "fresh", "upgrade", "verify"}, "unknown schema identity mode")
    require(profile in {"postgresql", "cockroachdb"} and profile in chains, "unknown schema profile")
    require(type(schema_version) is int and schema_version == 3, "unsupported current schema version")
    require(isinstance(chains[profile], dict) and type(chains[profile].get("file_count")) is int
            and chains[profile]["file_count"] == schema_version, "incomplete current source chain")
    require(isinstance(value, dict), "schema identity must be an object")
    require(value.get("schema") == "trillionnium.authoritative-schema-report.v1", "wrong schema report envelope")
    require(value.get("profile") == profile, "schema profile mismatch")
    require(type(value.get("schema_version")) is int and value["schema_version"] == schema_version, "schema version mismatch")
    require(type(value.get("storage_writer_epoch")) is int and value["storage_writer_epoch"] == 3, "storage writer epoch mismatch")
    require(value.get("digest_algorithm") == ALGORITHM, "schema digest algorithm mismatch")
    require(value.get("chain_digest") == chains[profile]["chain_sha256"], "complete migration chain digest mismatch")
    require(type(value.get("table_count")) is int and value["table_count"] == table_count, "schema table count mismatch")
    require(value.get("compatibility_credit") is False, "schema source identity cannot grant compatibility credit")
    for key in ("source_commit", "upgrade_source_commit", "v2_apply_source_commit"):
        require(isinstance(value.get(key), str) and re.fullmatch(r"[0-9a-fA-F]{40}", value[key]) is not None, "invalid schema provenance")
    if source_commit is not None:
        require(isinstance(source_commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", source_commit) is not None, "invalid expected apply commit")
    if v2_apply_source_commit is not None:
        require(isinstance(v2_apply_source_commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", v2_apply_source_commit) is not None,
                "invalid expected v2 apply commit")
        require(value["v2_apply_source_commit"] == v2_apply_source_commit, "preserved v2 apply provenance mismatch")
    if mode == "upgrade":
        require(type(from_version) is int and from_version in (1, 2), "upgrade requires the exact previous schema version")
    else:
        require(from_version is None, "previous schema version only applies to upgrade mode")
    applied = value.get("migration_applied")
    steps = value.get("applied_steps")
    require(type(applied) is bool and type(steps) is int, "invalid migration outcome types")
    require(0 <= steps <= chains[profile]["file_count"], "migration step count out of range")
    require((applied and steps > 0) or (not applied and steps == 0), "migration outcome and steps disagree")
    if mode == "fresh":
        require(applied and steps == chains[profile]["file_count"], "fresh schema did not execute the complete chain")
        require(value["source_commit"] == value["upgrade_source_commit"] == value["v2_apply_source_commit"],
                "fresh foundation/v2/current apply provenance mismatch")
        if source_commit is not None:
            require(value["source_commit"] == source_commit and value["upgrade_source_commit"] == source_commit, "fresh apply provenance mismatch")
    elif mode == "upgrade":
        require(applied and steps == schema_version - from_version,
                "upgrade did not complete the exact remaining migration suffix")
        if from_version == 1:
            require(value["v2_apply_source_commit"] == value["upgrade_source_commit"],
                    "v1 upgrade v2 apply provenance mismatch")
        else:
            require(v2_apply_source_commit is not None, "v2 upgrade requires preserved prior apply provenance")
        if source_commit is not None:
            require(value["upgrade_source_commit"] == source_commit, "upgrade apply provenance mismatch")
    elif mode == "verify":
        require(not applied and steps == 0, "read-only verification reported a mutation")
        # A later executable's SHA is not the original database apply SHA.
        require(source_commit is None, "read-only verify does not bind a binary source commit")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identity", type=Path)
    parser.add_argument("profile", choices=["postgresql", "cockroachdb"])
    parser.add_argument("--mode", choices=["any", "fresh", "upgrade", "verify"], default="any")
    parser.add_argument("--source-commit")
    parser.add_argument("--from-version", type=int, choices=[1, 2])
    parser.add_argument("--v2-apply-source-commit", help="Expected historical v2 publisher; required when upgrading from v2")
    arguments = parser.parse_args()
    try:
        data = arguments.identity.read_bytes()
        value = decode_identity_document(data)
        chains, version, tables = validated_source()
        validate_identity(value, profile=arguments.profile, chains=chains, schema_version=version,
                          table_count=tables, mode=arguments.mode, source_commit=arguments.source_commit,
                          from_version=arguments.from_version, v2_apply_source_commit=arguments.v2_apply_source_commit)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        # Stable check failures contain no database connection information.
        print(f"authoritative schema identity rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"schema": "trillionnium.authoritative-schema-identity-check.v1", "profile": arguments.profile,
                      "schema_version": version, "chain_digest": value["chain_digest"], "identity_verified": True,
                      "compatibility_credit": False}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
