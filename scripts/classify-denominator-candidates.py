#!/usr/bin/env python3
"""Classify generated denominator candidates using reviewed deterministic rules."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "docs/development/DENOMINATOR_CLASSIFICATION_RULES.json"


class ClassificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ClassificationError(message)


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClassificationError(f"{path}: {error}") from error


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def leaf_list(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("leaves", "items", "entries", "methods", "records"):
        value = candidate.get(key)
        if value is not None:
            require(isinstance(value, list), f"candidate {key} must be a list")
            require(all(isinstance(row, dict) for row in value), f"candidate {key} rows must be objects")
            return value
    raise ClassificationError("candidate must contain leaves, items, entries, methods or records")


def leaf_id(leaf: dict[str, Any], index: int) -> str:
    for key in ("leaf_id", "id", "key", "signature"):
        value = leaf.get(key)
        if isinstance(value, str) and value:
            return value
    digest = hashlib.sha256(canonical_bytes(leaf)).hexdigest()[:24]
    return f"generated:{index:06d}:{digest}"


def denominator_id(candidate: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit
    for key in ("denominator_id", "id", "denominator"):
        value = candidate.get(key)
        if isinstance(value, str) and value.startswith("DEN-"):
            return value
    filename = str(candidate.get("name", "")).upper()
    for value in (
        "DEN-SOURCE", "DEN-API", "DEN-RTAPI", "DEN-CONSOLE", "DEN-RUNTIME",
        "DEN-CONFIG", "DEN-CLI", "DEN-DB", "DEN-DATA", "DEN-METRICS",
        "DEN-OPS", "DEN-PROVIDERS", "DEN-IAP", "DEN-SDK",
    ):
        if value.removeprefix("DEN-") in filename:
            return value
    raise ClassificationError("candidate denominator ID is missing; pass --denominator-id")


def match_rule(rule: dict[str, Any], denominator: str, leaf: dict[str, Any]) -> bool:
    denominator_ids = rule.get("denominator_ids")
    if not isinstance(denominator_ids, list) or denominator not in denominator_ids:
        return False
    match = rule.get("match")
    require(isinstance(match, dict), f"{rule.get('id')}: match must be an object")
    if match.get("any") is True:
        return True

    searchable = "\n".join(
        str(value)
        for key, value in sorted(leaf.items())
        if key in {"leaf_id", "id", "key", "kind", "source", "source_path", "path", "name", "signature"}
    ).lower()
    prefixes = match.get("leaf_id_prefixes", [])
    contains = match.get("contains", [])
    kinds = match.get("kinds", [])
    if prefixes:
        current = str(leaf.get("leaf_id") or leaf.get("id") or leaf.get("key") or "")
        if not any(current.startswith(str(prefix)) for prefix in prefixes):
            return False
    if contains and not all(str(value).lower() in searchable for value in contains):
        return False
    if kinds and str(leaf.get("kind", "")) not in set(map(str, kinds)):
        return False
    return True


def validate_rules(rules: dict[str, Any]) -> list[dict[str, Any]]:
    require(rules.get("schema") == "trillionnium.denominator-classification-rules.v1", "wrong rules schema")
    require(rules.get("project_id") == "trillionnium-game", "wrong rules project")
    policy = rules.get("policy")
    require(isinstance(policy, dict), "rules policy missing")
    require(policy.get("exactly_one_rule_per_leaf") is True, "exactly-one policy must be true")
    require(policy.get("default_catch_all_allowed") is False, "global catch-all policy must be false")
    rows = rules.get("rules")
    require(isinstance(rows, list) and rows, "classification rules must be non-empty")
    ids: set[str] = set()
    denominator_coverage: set[str] = set()
    required = {
        "id", "denominator_ids", "match", "owner_role", "task_id", "test_id",
        "compatibility_profile", "parity_ids", "required_evidence_types",
    }
    for rule in rows:
        require(isinstance(rule, dict), "classification rule must be an object")
        missing = required - rule.keys()
        require(not missing, f"{rule.get('id')}: missing fields {sorted(missing)}")
        rule_id = rule["id"]
        require(isinstance(rule_id, str) and rule_id, "classification rule ID missing")
        require(rule_id not in ids, f"duplicate classification rule: {rule_id}")
        ids.add(rule_id)
        denominator_ids = rule["denominator_ids"]
        require(isinstance(denominator_ids, list) and denominator_ids, f"{rule_id}: denominator IDs missing")
        denominator_coverage.update(map(str, denominator_ids))
        for key in ("owner_role", "task_id", "test_id", "compatibility_profile"):
            require(isinstance(rule[key], str) and rule[key], f"{rule_id}: {key} missing")
        for key in ("parity_ids", "required_evidence_types"):
            require(isinstance(rule[key], list), f"{rule_id}: {key} must be a list")
    expected = {
        "DEN-SOURCE", "DEN-API", "DEN-RTAPI", "DEN-CONSOLE", "DEN-RUNTIME",
        "DEN-CONFIG", "DEN-CLI", "DEN-DB", "DEN-DATA", "DEN-METRICS",
        "DEN-OPS", "DEN-PROVIDERS", "DEN-IAP", "DEN-SDK",
    }
    require(denominator_coverage == expected, f"denominator rule coverage mismatch: {sorted(denominator_coverage ^ expected)}")
    return rows


def classify(candidate: dict[str, Any], rules: dict[str, Any], explicit_denominator: str | None) -> dict[str, Any]:
    rows = validate_rules(rules)
    denominator = denominator_id(candidate, explicit_denominator)
    leaves = leaf_list(candidate)
    require(leaves, f"{denominator}: candidate leaf collection is empty")

    output_leaves: list[dict[str, Any]] = []
    unclassified: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    seen: set[str] = set()
    for index, leaf in enumerate(leaves):
        current_id = leaf_id(leaf, index)
        require(current_id not in seen, f"{denominator}: duplicate leaf ID {current_id}")
        seen.add(current_id)
        matches = [rule for rule in rows if match_rule(rule, denominator, leaf)]
        if not matches:
            unclassified.append(current_id)
            continue
        if len(matches) > 1:
            ambiguous[current_id] = [str(rule["id"]) for rule in matches]
            continue
        rule = matches[0]
        classified = dict(leaf)
        classified.update(
            {
                "leaf_id": current_id,
                "denominator_id": denominator,
                "classification_rule_id": rule["id"],
                "owner_role": rule["owner_role"],
                "task_id": rule["task_id"],
                "test_id": rule["test_id"],
                "compatibility_profile": rule["compatibility_profile"],
                "parity_ids": rule["parity_ids"],
                "required_evidence_types": rule["required_evidence_types"],
                "implementation_status": "unimplemented",
                "verification_status": "unverified",
                "review_status": "pending",
                "evidence_ids": [],
                "compatibility_credit": False,
            }
        )
        output_leaves.append(classified)

    require(not unclassified, f"{denominator}: unclassified leaves: {unclassified[:20]}")
    require(not ambiguous, f"{denominator}: ambiguous leaves: {dict(list(ambiguous.items())[:20])}")
    require(len(output_leaves) == len(leaves), f"{denominator}: classified leaf count mismatch")

    rules_sha = hashlib.sha256(canonical_bytes(rules)).hexdigest()
    candidate_sha = hashlib.sha256(canonical_bytes(candidate)).hexdigest()
    payload = {
        "schema": "trillionnium.denominator-lock-candidate.v1",
        "project_id": "trillionnium-game",
        "denominator_id": denominator,
        "source_candidate_sha256": candidate_sha,
        "classification_rules_sha256": rules_sha,
        "leaf_count": len(output_leaves),
        "unclassified_count": 0,
        "ambiguous_count": 0,
        "leaves": sorted(output_leaves, key=lambda row: row["leaf_id"]),
        "review": {
            "decision": "pending",
            "reviewers": [],
        },
        "claims": {
            "classification_complete": True,
            "independently_reviewed": False,
            "lock_accepted": False,
            "sg1_complete": False,
            "compatibility_credit": False,
        },
    }
    payload["manifest_sha256"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    parser.add_argument("--denominator-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        candidate = load(args.candidate)
        require(isinstance(candidate, dict), "candidate root must be an object")
        candidate.setdefault("name", args.candidate.name)
        rules = load(args.rules)
        require(isinstance(rules, dict), "rules root must be an object")
        result = classify(candidate, rules, args.denominator_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except ClassificationError as error:
        print(f"denominator classification failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "denominator_id": result["denominator_id"],
                "leaf_count": result["leaf_count"],
                "manifest_sha256": result["manifest_sha256"],
                "lock_accepted": False,
                "compatibility_credit": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
