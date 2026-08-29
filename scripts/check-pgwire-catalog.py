#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

EXPECTED_TABLES = {
    "trnm_authority_leases",
    "trnm_command_outbox",
    "trnm_command_receipts",
    "trnm_entity_heads",
    "trnm_events",
    "trnm_outbox",
    "trnm_refresh_tokens",
    "trnm_schema_metadata",
    "trnm_session_families",
    "trnm_storage_objects",
}
FORBIDDEN_COLUMN_FRAGMENTS = {"raw_token", "access_token", "refresh_token_value", "secret_key"}


def read_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", type=Path, required=True)
    parser.add_argument("--columns", type=Path, required=True)
    parser.add_argument("--constraints", type=Path, required=True)
    parser.add_argument("--indexes", type=Path, required=True)
    args = parser.parse_args()

    tables = set(read_lines(args.tables))
    if tables != EXPECTED_TABLES:
        raise SystemExit(
            f"table mismatch: missing={sorted(EXPECTED_TABLES - tables)} "
            f"extra={sorted(tables - EXPECTED_TABLES)}"
        )

    columns = read_lines(args.columns)
    if not columns:
        raise SystemExit("column inventory is empty")
    lowered = "\n".join(columns).lower()
    for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
        if fragment in lowered:
            raise SystemExit(f"forbidden secret-bearing column fragment: {fragment}")
    if "trnm_refresh_tokens|token_digest" not in lowered:
        raise SystemExit("refresh token digest column is missing")

    constraints = read_lines(args.constraints)
    indexes = read_lines(args.indexes)
    if not constraints or not indexes:
        raise SystemExit("constraint or index inventory is empty")
    if not any(
        "primary key" in line.lower() or "_pkey" in line.lower()
        for line in constraints + indexes
    ):
        raise SystemExit("primary-key evidence is missing")
    print(
        "catalog contract passed: "
        f"tables={len(tables)} columns={len(columns)} "
        f"constraints={len(constraints)} indexes={len(indexes)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
