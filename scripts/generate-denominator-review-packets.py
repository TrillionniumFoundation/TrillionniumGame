#!/usr/bin/env python3
"""Generate immutable, leaf-complete denominator family review packets."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "docs/development/PARITY_DENOMINATORS.json"
WORKLIST = ROOT / "manifests/upstream/denominator-review-worklist.json"
CANDIDATES = ROOT / "manifests/upstream/candidates"
DEFAULT_OUTPUT = ROOT / "docs/review/denominator-family-packets"
ID_KEYS = ("leaf_id", "stable_id", "id", "path", "symbol", "name", "key")
COLLECTION_KEYS = (
    "leaves", "entries", "items", "operations", "methods", "symbols",
    "members", "fields", "records", "surface", "endpoints", "routes",
)

class PacketError(RuntimeError):
    pass

def require(value: bool, message: str) -> None:
    if not value:
        raise PacketError(message)

def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()

def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())

def scalar_by_key(value: Any, keys: set[str]) -> int | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, int) and not isinstance(item, bool):
                return item
        for item in value.values():
            found = scalar_by_key(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = scalar_by_key(item, keys)
            if found is not None:
                return found
    return None

def manifest_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.endswith(".json") and "manifests/upstream/candidates" in value:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from manifest_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from manifest_strings(item)

def discover_manifests() -> list[Path]:
    paths: list[Path] = []
    if WORKLIST.is_file():
        worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
        for raw in manifest_strings(worklist):
            path = ROOT / raw
            if path.is_file() and path not in paths:
                paths.append(path)
    if CANDIDATES.is_dir():
        for path in sorted(CANDIDATES.rglob("*.json")):
            if path not in paths:
                paths.append(path)
    filtered = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if choose_leaf_collection(value, allow_empty=True) is not None:
            filtered.append(path)
    return filtered

def walk_lists(value: Any, pointer: str = "") -> Iterable[tuple[int, int, str, list[Any]]]:
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            child = f"{pointer}/{escaped}"
            if isinstance(item, list) and item:
                recognized = 1 if key.lower() in COLLECTION_KEYS else 0
                structured = sum(isinstance(row, dict) for row in item)
                yield (recognized, structured, child, item)
            yield from walk_lists(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_lists(item, f"{pointer}/{index}")

def choose_leaf_collection(value: Any, allow_empty: bool = False) -> tuple[str, list[Any]] | None:
    candidates = list(walk_lists(value))
    if not candidates:
        if allow_empty:
            return None
        raise PacketError("manifest has no non-empty candidate collection")
    # Prefer explicitly named leaf collections, then structured rows, then size.
    recognized = [row for row in candidates if row[0] == 1]
    pool = recognized or candidates
    pool.sort(key=lambda row: (row[1], len(row[3]), -row[2].count("/")), reverse=True)
    _, _, pointer, rows = pool[0]
    require(all(isinstance(row, (dict, str, int)) for row in rows), f"unsupported leaf collection at {pointer}")
    return pointer, rows

def family_id(path: Path, value: Any) -> str:
    if isinstance(value, dict):
        for key in ("family_id", "family", "denominator_id", "id", "name"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", item.strip()).strip("-")
                if candidate:
                    return candidate
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-")

def leaf_id(row: Any, index: int) -> str:
    if isinstance(row, dict):
        for key in ID_KEYS:
            item = row.get(key)
            if isinstance(item, (str, int)) and str(item).strip():
                return str(item)
    if isinstance(row, (str, int)):
        return str(row)
    return f"sha256:{digest_bytes(canonical(row))}"

def packet_for(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    selected = choose_leaf_collection(value)
    assert selected is not None
    pointer, rows = selected
    seen: dict[str, int] = {}
    leaves = []
    for index, row in enumerate(rows):
        raw_id = leaf_id(row, index)
        occurrence = seen.get(raw_id, 0)
        seen[raw_id] = occurrence + 1
        stable_id = raw_id if occurrence == 0 else f"{raw_id}#duplicate-{occurrence}"
        leaves.append({
            "leaf_id": stable_id,
            "source_index": index,
            "source_leaf_sha256": digest_bytes(canonical(row)),
            "classification": None,
            "rationale": None,
            "evidence_ids": [],
        })
    require(leaves, f"{path}: empty leaf set")
    relative = path.relative_to(ROOT).as_posix()
    return {
        "schema": "trillionnium.denominator-family-review-packet.v1",
        "family_id": family_id(path, value),
        "source_manifest": relative,
        "source_manifest_sha256": digest_file(path),
        "source_leaf_pointer": pointer,
        "leaf_count": len(leaves),
        "leaves": leaves,
        "review_binding": {
            "candidate_repository": "TrillionniumFoundation/TrillionniumGame",
            "source_head": None,
            "source_tree": None,
            "prospective_merge": None,
            "prospective_merge_tree": None,
            "reviewer_login": None,
            "reviewer_role": None,
            "conflict_free_attestation": None,
            "decision": None,
        },
        "claim_boundary": {
            "family_classified": False,
            "family_accepted": False,
            "global_sg1_accepted": False,
            "complete_nakama_compatibility": False,
            "production_ready": False,
        },
    }

def generate(output: Path) -> dict[str, Any]:
    parity = json.loads(PARITY.read_text(encoding="utf-8"))
    expected_families = scalar_by_key(parity, {"family_count", "denominator_family_count"}) or 14
    expected_leaves = scalar_by_key(parity, {"candidate_leaf_count", "leaf_count", "total_leaf_count"}) or 10173
    manifests = discover_manifests()
    require(len(manifests) == expected_families, f"expected {expected_families} candidate manifests, found {len(manifests)}")
    packets = [packet_for(path) for path in manifests]
    ids = [packet["family_id"] for packet in packets]
    require(len(ids) == len(set(ids)), "duplicate family IDs")
    packets.sort(key=lambda packet: packet["family_id"])
    total = sum(packet["leaf_count"] for packet in packets)
    require(total == expected_leaves, f"expected {expected_leaves} leaves, extracted {total}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    rows = []
    for packet in packets:
        path = output / f"{packet['family_id']}.candidate.json"
        path.write_bytes(canonical(packet))
        rows.append({
            "family_id": packet["family_id"],
            "packet": path.name,
            "packet_sha256": digest_file(path),
            "source_manifest": packet["source_manifest"],
            "source_manifest_sha256": packet["source_manifest_sha256"],
            "leaf_count": packet["leaf_count"],
        })
    index = {
        "schema": "trillionnium.denominator-family-review-index.v1",
        "parity_authority": PARITY.relative_to(ROOT).as_posix(),
        "parity_authority_sha256": digest_file(PARITY),
        "review_worklist": WORKLIST.relative_to(ROOT).as_posix(),
        "review_worklist_sha256": digest_file(WORKLIST),
        "family_count": len(rows),
        "leaf_count": total,
        "families": rows,
        "global_sg1_decision": None,
        "claim_boundary": {
            "all_families_classified": False,
            "all_families_accepted": False,
            "global_sg1_accepted": False,
            "complete_nakama_compatibility": False,
            "production_ready": False,
        },
    }
    (output / "index.json").write_bytes(canonical(index))
    return index

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = generate(args.output.resolve())
    print(json.dumps({"families": report["family_count"], "leaves": report["leaf_count"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
