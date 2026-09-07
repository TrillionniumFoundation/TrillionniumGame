#!/usr/bin/env python3
"""Materialize reviewable scope/reviewer blockers without granting acceptance.

This tool is intentionally conservative.  It discovers the pinned D0-D8
manifests, turns existing per-leaf proposals into deterministic sidecar review
bundles, and records reviewer-capacity shortages.  It never edits GAP_REGISTER,
PRODUCT_GATES or the evidence index and never changes a product/compatibility
claim to true.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
MANIFEST_ROOT = ROOT / "manifests/upstream"
OUT_ROOT = ROOT / "docs/status/denominator-classification"
INDEX = ROOT / "docs/status/DENOMINATOR_CLASSIFICATION_CANDIDATE.json"
CAPACITY = ROOT / "docs/status/REVIEWER_CAPACITY.json"
WAVE = ROOT / "docs/status/WAVE3_BLOCKERS.json"
EXPECTED_FAMILIES = 14
EXPECTED_LEAVES = 10_173

CLASS_KEYS = ("classification", "compatibility_classification", "disposition")
PROPOSAL_KEYS = (
    "classification_proposal",
    "proposed_classification",
    "suggested_classification",
    "classification_candidate",
    "proposed_disposition",
)
IDENTITY_KEYS = ("leaf_id", "id", "key", "symbol", "name", "path")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if compact
        else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    )
    path.write_text(text + "\n", encoding="utf-8")


def pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def walk(value: Any, pointer: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield pointer or "/", value
        for key, child in value.items():
            yield from walk(child, pointer + "/" + pointer_part(str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, pointer + f"/{index}")


def first_string(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def is_leaf(row: dict[str, Any]) -> bool:
    current = first_string(row, CLASS_KEYS)
    proposal = first_string(row, PROPOSAL_KEYS)
    if proposal is not None:
        return True
    if current is not None and normalize(current) == "unclassified":
        return True
    # Existing denominator leaves carry execution/evidence fields even where
    # older generators omitted an explicit proposal key.
    marker_count = sum(
        key in row
        for key in (
            "owner",
            "owner_role",
            "task",
            "task_id",
            "test",
            "test_id",
            "profile",
            "evidence",
            "evidence_ids",
        )
    )
    return marker_count >= 4 and first_string(row, IDENTITY_KEYS) is not None


def conservative_proposal(row: dict[str, Any]) -> tuple[str, str]:
    proposal = first_string(row, PROPOSAL_KEYS)
    if proposal and normalize(proposal) != "unclassified":
        return normalize(proposal), "existing-manifest-proposal"
    current = first_string(row, CLASS_KEYS)
    if current and normalize(current) != "unclassified":
        return normalize(current), "existing-explicit-classification"
    text = json.dumps(row, ensure_ascii=False, sort_keys=True).lower()
    if any(token in text for token in ("unsupported", "out-of-scope", "out_of_scope")):
        return "unsupported", "explicit-unsupported-boundary"
    if any(token in text for token in ("defer", "future", "post-sg1", "post_sg1")):
        return "deferred", "explicit-deferred-boundary"
    # This is a real classification state, but deliberately earns no feature
    # or compatibility credit until independent review accepts it.
    return "review-required", "fail-closed-no-explicit-proposal"


def manifest_candidates() -> list[Path]:
    candidates: list[tuple[Path, int]] = []
    for path in sorted(MANIFEST_ROOT.rglob("*.json")):
        try:
            data = load(path)
        except (OSError, ValueError, RecursionError):
            continue
        count = sum(1 for _, row in walk(data) if is_leaf(row))
        if count:
            candidates.append((path, count))
    # The authoritative set has fourteen non-empty denominator manifests.
    if len(candidates) != EXPECTED_FAMILIES:
        observed = ", ".join(f"{p.relative_to(ROOT)}={n}" for p, n in candidates)
        raise SystemExit(
            f"expected {EXPECTED_FAMILIES} denominator manifests, observed "
            f"{len(candidates)}: {observed}"
        )
    return [path for path, _ in candidates]


def materialize_scope() -> dict[str, Any]:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for stale in OUT_ROOT.glob("*.json"):
        stale.unlink()

    families: list[dict[str, Any]] = []
    total = 0
    classification_counts: Counter[str] = Counter()
    seen: set[str] = set()
    for path in manifest_candidates():
        data = load(path)
        rows: list[list[str]] = []
        for pointer, leaf in walk(data):
            if not is_leaf(leaf):
                continue
            identity = first_string(leaf, IDENTITY_KEYS) or pointer
            stable_id = f"{path.relative_to(ROOT).as_posix()}#{pointer}:{identity}"
            if stable_id in seen:
                raise SystemExit(f"duplicate denominator leaf identity: {stable_id}")
            seen.add(stable_id)
            proposal, reason = conservative_proposal(leaf)
            if proposal == "unclassified":
                raise SystemExit(f"unclassified proposal survived: {stable_id}")
            rows.append([pointer, identity, proposal, reason])
            classification_counts[proposal] += 1
        total += len(rows)
        family_name = path.stem
        slug = normalize(path.relative_to(MANIFEST_ROOT).with_suffix("").as_posix())
        target = OUT_ROOT / f"{slug}.json"
        payload = {
            "schema": "trillionnium.denominator-classification-review.v1",
            "source": path.relative_to(ROOT).as_posix(),
            "family": family_name,
            "leaf_count": len(rows),
            "unclassified_count": 0,
            "accepted": False,
            "sg1_credit": False,
            "compatibility_credit": False,
            "production_ready": False,
            "rows": rows,
        }
        dump(target, payload, compact=True)
        raw = target.read_bytes()
        families.append(
            {
                "family": family_name,
                "source": path.relative_to(ROOT).as_posix(),
                "review_bundle": target.relative_to(ROOT).as_posix(),
                "leaf_count": len(rows),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    if total != EXPECTED_LEAVES:
        raise SystemExit(f"expected {EXPECTED_LEAVES} leaves, observed {total}")
    index = {
        "schema": "trillionnium.denominator-classification-candidate.v1",
        "family_count": len(families),
        "leaf_count": total,
        "unclassified_count": 0,
        "classification_counts": dict(sorted(classification_counts.items())),
        "families": families,
        "review_state": "independent-family-and-global-sg1-review-required",
        "accepted": False,
        "gap_closed": False,
        "sg1_credit": False,
        "compatibility_credit": False,
        "production_ready": False,
        "public_online": False,
        "nakama_retired": False,
    }
    dump(INDEX, index)
    return index


def materialize_reviewer_capacity() -> dict[str, Any]:
    domains = (
        "program-governance",
        "security-cryptography",
        "database-data-integrity",
        "protocol-realtime",
        "compatibility-qa",
        "sre-operations",
    )
    value = {
        "schema": "trillionnium.reviewer-capacity.v1",
        "policy_minimum_eligible_reviewers": 2,
        "candidate_authors_or_mutators": ["Franksudoman", "ProfHepta"],
        "eligible_conflict_free_reviewers": ["Tomasrgbsf"],
        "eligible_reviewer_count": 1,
        "capacity_satisfied": False,
        "domains": [
            {
                "domain": domain,
                "status": "blocked-reviewer-capacity",
                "required": 2,
                "available": 1,
                "accepted": False,
            }
            for domain in domains
        ],
        "claim_boundary": {
            "independent_review_complete": False,
            "accepted_evidence": False,
            "gap_closed": False,
            "production_ready": False,
        },
    }
    dump(CAPACITY, value)
    return value


def materialize_wave(scope: dict[str, Any], capacity: dict[str, Any]) -> None:
    value = {
        "schema": "trillionnium.gap-closure-wave.v3",
        "plan": "v3.1",
        "source_base": "88c02a417c7b0c64f27dc9f49e0b5e6317150964",
        "completed_repository_controls": [
            "deterministic-denominator-classification-review-bundles",
            "zero-unclassified-candidate-materialization",
            "conflict-aware-reviewer-capacity-diagnostic",
        ],
        "scope": {
            "families": scope["family_count"],
            "leaves": scope["leaf_count"],
            "unclassified": scope["unclassified_count"],
            "accepted": False,
        },
        "reviewer_capacity": {
            "required": capacity["policy_minimum_eligible_reviewers"],
            "available": capacity["eligible_reviewer_count"],
            "satisfied": capacity["capacity_satisfied"],
        },
        "external_or_independent_blockers": [
            "second-conflict-free-reviewer",
            "six-specialist-current-object-decisions",
            "separate-global-sg1-acceptance",
            "effective-github-protection-and-no-bypass-readback",
            "real-kms-ha-pitr-capacity-migration-cutover-retirement-evidence",
        ],
        "accepted_evidence": False,
        "all_gaps_closed": False,
        "production_ready": False,
        "public_online": False,
        "nakama_retired": False,
    }
    dump(WAVE, value)


def verify_truth_boundary() -> None:
    for path in (INDEX, CAPACITY, WAVE):
        value = load(path)
        text = json.dumps(value, sort_keys=True)
        if '"accepted": true' in text or '"gap_closed": true' in text:
            raise SystemExit(f"forbidden acceptance claim in {path.relative_to(ROOT)}")
        if '"production_ready": true' in text or '"public_online": true' in text:
            raise SystemExit(f"forbidden production claim in {path.relative_to(ROOT)}")


def main() -> int:
    scope = materialize_scope()
    capacity = materialize_reviewer_capacity()
    materialize_wave(scope, capacity)
    verify_truth_boundary()
    print(
        json.dumps(
            {
                "families": scope["family_count"],
                "leaves": scope["leaf_count"],
                "unclassified": scope["unclassified_count"],
                "eligible_reviewers": capacity["eligible_reviewer_count"],
                "reviewer_capacity_satisfied": capacity["capacity_satisfied"],
                "accepted": False,
                "gap_closed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
