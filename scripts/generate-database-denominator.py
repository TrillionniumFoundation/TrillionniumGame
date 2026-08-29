#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.denominator.sql_migration_surface import classify_statement, split_sections, split_statements
from tools.upstream.pinned_archive import SourceArchiveError, git_blob_sha1_bytes, verify_source_lock

REPOSITORY = "heroiclabs/nakama"
COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
GENERATOR = "trillionniumgame-database-denominator"
VERSION = "0.1.0"


class DenominatorError(RuntimeError):
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


def stable_id(denominator: str, item: dict[str, Any]) -> str:
    seed = canonical(
        {
            "denominator": denominator,
            "class": item["class"],
            "path": item["path"],
            "symbol": item["symbol"],
            "signature": item["signature"],
            "direction": item.get("direction"),
        }
    )
    return "TG-D6-" + hashlib.sha256(seed).hexdigest()[:20].upper()


def source(root: Path, path: str, start: int | None, end: int | None) -> dict[str, Any]:
    data = (root / path).read_bytes()
    return {
        "repository": REPOSITORY,
        "commit": COMMIT,
        "path": path,
        "blob": git_blob_sha1_bytes(data),
        "sha256": sha256(data),
        "start_line": start,
        "end_line": end,
    }


def make_leaf(root: Path, denominator: str, item: dict[str, Any]) -> dict[str, Any]:
    identifier = stable_id(denominator, item)
    contract = {
        "class": item["class"],
        "symbol": item["symbol"],
        "signature": item["signature"],
        "direction": item.get("direction"),
        "metadata": item.get("metadata") or {},
    }
    task = "TG-W1-004" if denominator == "DEN-DB" else "TG-W14-002"
    return {
        "id": identifier,
        "layer": "D6",
        "denominator": denominator,
        "class": item["class"],
        "symbol": item["symbol"],
        "signature_hash": sha256(canonical(contract)),
        "source": source(root, item["path"], item.get("start_line"), item.get("end_line")),
        "compatibility_profile": "C2" if denominator == "DEN-DB" else "C3",
        "stability_tier": "data-contract",
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": "database-migration",
        "workstream": "W1" if denominator == "DEN-DB" else "W14",
        "task_ids": ["TG-W0-002", task],
        "test_ids": [f"TG-DIFF-{identifier}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def build_manifest(
    denominator: str,
    leaves: list[dict[str, Any]],
    manual: list[dict[str, Any]],
    source_lock: dict[str, Any],
    migration_count: int,
) -> dict[str, Any]:
    leaves.sort(key=lambda leaf: leaf["id"])
    if len({leaf["id"] for leaf in leaves}) != len(leaves):
        raise DenominatorError(f"duplicate stable IDs in {denominator}")
    counts: dict[str, int] = {}
    for leaf in leaves:
        counts[leaf["class"]] = counts.get(leaf["class"], 0) + 1
    value: dict[str, Any] = {
        "schema": "trillionnium.database-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "generator": {"name": GENERATOR, "version": VERSION},
        "denominator": denominator,
        "layer": "D6",
        "status": "candidate-unclassified",
        "migration_file_count": migration_count,
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "unreviewed_count": len(leaves),
        "manual_contract_count": len(manual),
        "counts_by_class": counts,
        "source_lock": source_lock,
        "leaves": leaves,
        "manual_contracts": sorted(manual, key=canonical),
        "claims": {
            "sg1_complete": False,
            "schema_equivalent": False,
            "data_semantics_equivalent": False,
            "migration_compatible": False,
            "rollback_proven": False,
            "production_ready": False,
        },
    }
    value["content_sha256"] = sha256(canonical(value))
    return value


def generate(root: Path, output: Path) -> dict[str, Any]:
    try:
        source_lock = verify_source_lock(
            root,
            repository=REPOSITORY,
            revision=COMMIT,
            tree=TREE,
        )
    except SourceArchiveError as exc:
        raise DenominatorError(str(exc)) from exc

    files = sorted((root / "migrate/sql").glob("*.sql"))
    if not files:
        raise DenominatorError("no migration SQL files found")

    db_items: list[dict[str, Any]] = []
    data_items: list[dict[str, Any]] = []
    db_manual: list[dict[str, Any]] = []
    data_manual: list[dict[str, Any]] = []
    migration_summary: list[dict[str, Any]] = []
    db_classes = {
        "migration_file",
        "migration_section",
        "sql_statement",
        "db_table",
        "db_column",
        "db_constraint",
        "db_inline_constraint",
        "db_alter_table_action",
        "db_index",
        "db_drop_table",
        "db_drop_index",
        "db_drop_sequence",
        "db_drop_type",
        "db_drop_view",
        "db_sequence",
        "db_type",
        "db_view",
        "db_function",
        "db_trigger",
        "db_permission",
        "db_control_statement",
    }
    data_classes = {
        "data_backfill",
        "data_invariant_candidate",
        "data_default_candidate",
    }

    for order, file in enumerate(files, 1):
        path = file.relative_to(root).as_posix()
        text = file.read_text(encoding="utf-8")
        sections = split_sections(text)
        directions = [direction for direction, _, _, _ in sections]
        db_items.append(
            {
                "class": "migration_file",
                "symbol": path,
                "signature": sha256(file.read_bytes()),
                "path": path,
                "direction": "all",
                "start_line": 1,
                "end_line": text.count("\n") + 1,
                "metadata": {
                    "order": str(order),
                    "has_up": str("up" in directions).lower(),
                    "has_down": str("down" in directions).lower(),
                },
            }
        )
        statement_count = 0
        for direction, segment, start, end in sections:
            db_items.append(
                {
                    "class": "migration_section",
                    "symbol": f"{path}.{direction}",
                    "signature": sha256(segment.encode("utf-8")),
                    "path": path,
                    "direction": direction,
                    "start_line": start,
                    "end_line": end,
                    "metadata": {},
                }
            )
            for statement in split_statements(segment, direction, start):
                statement_count += 1
                items, manual = classify_statement(statement, path)
                for item in items:
                    if item["class"] in db_classes:
                        db_items.append(item)
                    if item["class"] in data_classes:
                        data_items.append(item)
                db_manual.extend(manual)
        if "up" not in directions:
            db_manual.append(
                {
                    "class": "migration_missing_up",
                    "symbol": path,
                    "path": path,
                    "direction": "all",
                    "start_line": 1,
                    "end_line": 1,
                    "signature": "missing up marker",
                }
            )
        if "down" not in directions:
            db_manual.append(
                {
                    "class": "migration_missing_down",
                    "symbol": path,
                    "path": path,
                    "direction": "all",
                    "start_line": 1,
                    "end_line": 1,
                    "signature": "missing down marker",
                }
            )
        migration_summary.append(
            {
                "path": path,
                "order": order,
                "directions": directions,
                "statement_count": statement_count,
            }
        )

    db_leaves = [make_leaf(root, "DEN-DB", item) for item in db_items]
    data_leaves = [make_leaf(root, "DEN-DATA", item) for item in data_items]
    db_manifest = build_manifest("DEN-DB", db_leaves, db_manual, source_lock, len(files))
    data_manifest = build_manifest("DEN-DATA", data_leaves, data_manual, source_lock, len(files))
    reconciliation = {
        "schema": "trillionnium.database-data-reconciliation.v1",
        "status": "candidate-unreviewed",
        "migration_files": migration_summary,
        "db_manifest_sha256": db_manifest["content_sha256"],
        "data_manifest_sha256": data_manifest["content_sha256"],
        "migration_order_proven_from_filename": True,
        "down_section_presence_proves_reversibility": False,
        "postgresql_compatibility_proven": False,
        "cockroachdb_compatibility_proven": False,
        "compatibility_credit": False,
    }

    output.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("database-denominator.candidate.json", db_manifest),
        ("data-denominator.candidate.json", data_manifest),
        ("database-data-reconciliation.candidate.json", reconciliation),
    ):
        (output / name).write_bytes(canonical(value) + b"\n")
    checksums = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(output.glob("*.json"))
    ]
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return {
        "migration_file_count": len(files),
        "db_leaf_count": len(db_leaves),
        "data_leaf_count": len(data_leaves),
        "manual_contract_count": len(db_manual) + len(data_manual),
        "sg1_complete": False,
        "compatibility_credit": False,
    }


def require_sg1(output: Path) -> None:
    failures: list[str] = []
    for name in (
        "database-denominator.candidate.json",
        "data-denominator.candidate.json",
    ):
        value = json.loads((output / name).read_text(encoding="utf-8"))
        if value.get("status") != "reviewed-locked":
            failures.append(f"{name} is not reviewed-locked")
        if value.get("unclassified_count") != 0 or value.get("unreviewed_count") != 0:
            failures.append(f"{name} has open classification or review work")
        if value.get("manual_contract_count") != 0:
            failures.append(f"{name} has unresolved manual contracts")
        if value.get("claims", {}).get("sg1_complete") is not True:
            failures.append(f"{name} SG1 claim remains false")
    if failures:
        raise DenominatorError("SG1 remains open: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nakama-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-sg1", action="store_true")
    args = parser.parse_args()
    try:
        result = generate(args.nakama_dir.resolve(), args.output_dir.resolve())
        if args.require_sg1:
            require_sg1(args.output_dir.resolve())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DenominatorError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"database denominator generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
