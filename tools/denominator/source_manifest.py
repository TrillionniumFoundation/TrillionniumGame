from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}")


class SourceManifestError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SourceManifestError(f"{path}: root must be an object")
    return value


def _leaf(item_class: str, symbol: str, contract: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    seed = canonical_bytes({"class": item_class, "symbol": symbol, "contract": contract})
    leaf_id = "TG-D0-SOURCE-" + hashlib.sha256(seed).hexdigest()[:18].upper()
    return {
        "id": leaf_id,
        "layer": "D0-upstream-source-build",
        "class": item_class,
        "symbol": symbol,
        "source": source,
        "signature_hash": sha256_bytes(seed),
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": "governance",
        "workstream": "W0",
        "task_ids": ["TG-W0-002"],
        "test_ids": [f"TG-DIFF-{leaf_id}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def generate(root: Path = ROOT) -> dict[str, Any]:
    baseline_path = root / "docs/development/UPSTREAM_BASELINE.json"
    sdk_path = root / "config/sdk-source-snapshots.json"
    images_path = root / "config/database-test-images.json"
    baseline = _load(baseline_path)
    sdk = _load(sdk_path)
    images = _load(images_path)
    leaves: list[dict[str, Any]] = []

    for key in ("nakama", "nakama_common"):
        row = baseline[key]
        identity = {
            "repository": row["repository"],
            "tag": row.get("tag"),
            "commit": row["commit"],
            "tree": row["tree"],
        }
        leaves.append(_leaf("upstream_source_root", row["repository"], identity, identity))
        for group in ("source_roots", "protocol_contracts", "implementation_contracts"):
            for item in row.get(group, []):
                contract = {"repository": row["repository"], "group": group, **item}
                leaves.append(
                    _leaf(
                        "upstream_source_object",
                        f"{row['repository']}:{item['path']}",
                        contract,
                        identity,
                    )
                )

    profiles = sdk.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise SourceManifestError("SDK source registry is empty")
    for profile in profiles:
        contract = {
            key: profile.get(key)
            for key in ("id", "repository", "branch", "commit", "tree", "archive_tree", "language", "platform")
            if profile.get(key) is not None
        }
        contract["gitlinks"] = profile.get("gitlinks", [])
        contract["archive_repairs"] = profile.get("archive_repairs", [])
        leaves.append(
            _leaf(
                "sdk_source_root",
                f"{profile['id']}:{profile['repository']}",
                contract,
                {"path": "config/sdk-source-snapshots.json"},
            )
        )

    profiles_value = images.get("profiles")
    if not isinstance(profiles_value, dict) or not profiles_value:
        raise SourceManifestError("database image registry is empty")
    for profile, row in sorted(profiles_value.items()):
        leaves.append(
            _leaf(
                "database_test_image",
                f"{profile}:{row['image']}",
                {"profile": profile, **row},
                {"path": "config/database-test-images.json"},
            )
        )

    lock_paths = [
        "rust-toolchain.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "runtime/go.mod",
        "runtime/go.sum",
        "scripts/blackbox/package-lock.json",
        "config/database-test-images.json",
        "config/sdk-source-snapshots.json",
    ]
    for relative in lock_paths:
        path = root / relative
        if not path.is_file():
            continue
        payload = path.read_bytes()
        leaves.append(
            _leaf(
                "toolchain_or_dependency_lock",
                relative,
                {"path": relative, "size_bytes": len(payload), "sha256": sha256_bytes(payload)},
                {"path": relative},
            )
        )

    digest_paths = [
        "compose.yaml",
        "runtime/Dockerfile",
        "runtime/Dockerfile.faultlab",
        "config/database-test-images.json",
    ]
    found: set[str] = set()
    for relative in digest_paths:
        path = root / relative
        if not path.is_file():
            continue
        for reference in DIGEST.findall(path.read_text(encoding="utf-8")):
            if reference in found:
                continue
            found.add(reference)
            leaves.append(
                _leaf(
                    "oci_digest_reference",
                    reference,
                    {"reference": reference, "declared_in": relative},
                    {"path": relative},
                )
            )

    leaves.sort(key=lambda item: item["id"])
    ids = [item["id"] for item in leaves]
    if not leaves or len(ids) != len(set(ids)):
        raise SourceManifestError("source denominator is empty or contains duplicate IDs")
    registry_digest = sha256_bytes(
        canonical_bytes(
            {
                "baseline": baseline,
                "sdk": sdk,
                "images": images,
            }
        )
    )
    return {
        "schema": "trillionnium.source-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "denominator": "DEN-SOURCE",
        "layer": "D0-upstream-source-build",
        "status": "candidate-unclassified",
        "source_registry_sha256": registry_digest,
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "unreviewed_count": len(leaves),
        "manual_contract_count": 0,
        "manual_contracts": [],
        "leaves": leaves,
        "claims": {
            "complete_source_inventory": False,
            "independently_reviewed": False,
            "sg1_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
        "sg1_eligible": False,
        "compatibility_credit": False,
    }
