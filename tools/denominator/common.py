# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tools.upstream.pinned_archive import git_blob_sha1_bytes, verify_source_lock

REPOSITORY = "heroiclabs/nakama"
REVISION = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
GENERATOR_VERSION = "0.2.0"


class DenominatorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceRef:
    repository: str
    commit: str
    path: str
    blob: str
    sha256: str
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "commit": self.commit,
            "path": self.path,
            "blob": self.blob,
            "sha256": self.sha256,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")


def verify_root(root: Path) -> dict[str, Any]:
    try:
        return verify_source_lock(root, repository=REPOSITORY, revision=REVISION, tree=TREE)
    except Exception as exc:
        raise DenominatorError(f"pinned Nakama source lock rejected: {exc}") from exc


def source_ref(root: Path, relative: str, start: int | None = None, end: int | None = None) -> SourceRef:
    path = root / relative
    if not path.is_file():
        raise DenominatorError(f"missing source file: {relative}")
    data = path.read_bytes()
    return SourceRef(REPOSITORY, REVISION, relative, git_blob_sha1_bytes(data), sha256(data), start, end)


def stable_id(
    layer: str,
    item_class: str,
    symbol: str,
    signature: Any,
    source: SourceRef | None = None,
) -> str:
    source_identity = None
    if source is not None:
        source_identity = {
            "repository": source.repository,
            "commit": source.commit,
            "path": source.path,
            "blob": source.blob,
            "start_line": source.start_line,
            "end_line": source.end_line,
        }
    seed = canonical_bytes(
        {
            "layer": layer,
            "class": item_class,
            "symbol": symbol,
            "signature": signature,
            "source": source_identity,
        }
    )
    return f"TG-{layer}-{hashlib.sha256(seed).hexdigest()[:18].upper()}"


def leaf(layer: str, item_class: str, symbol: str, source: SourceRef, contract: dict[str, Any], *, owner: str, workstream: str, task: str) -> dict[str, Any]:
    # Symbol names are not globally unique in protobuf, TypeScript or nested
    # configuration declarations. Bind the candidate leaf ID to the immutable
    # upstream source location as well as its normalized contract so duplicate
    # short names cannot collapse into one denominator identity.
    identifier = stable_id(layer, item_class, symbol, contract, source)
    return {
        "id": identifier,
        "layer": layer,
        "class": item_class,
        "symbol": symbol,
        "source": source.to_dict(),
        "signature_hash": sha256(canonical_bytes(contract)),
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": owner,
        "workstream": workstream,
        "task_ids": [task],
        "test_ids": [f"TG-DIFF-{identifier}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def manifest(denominator: str, layer: str, leaves: list[dict[str, Any]], manual: list[dict[str, Any]], *, generator: str) -> dict[str, Any]:
    leaves = sorted(leaves, key=lambda item: item["id"])
    ids = [item["id"] for item in leaves]
    if len(ids) != len(set(ids)):
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        raise DenominatorError(f"duplicate stable IDs in {denominator}: {duplicates[:5]}")
    value = {
        "schema": "trillionnium.parity-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "generator": {"name": generator, "version": GENERATOR_VERSION},
        "upstream": {"repository": REPOSITORY, "commit": REVISION, "tree": TREE},
        "denominator": denominator,
        "layer": layer,
        "status": "candidate-unclassified",
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "manual_contract_count": len(manual),
        "sg1_eligible": False,
        "compatibility_credit": False,
        "production_ready": False,
        "leaves": leaves,
        "manual_contracts": sorted(manual, key=canonical_bytes),
    }
    value["content_sha256"] = sha256(canonical_bytes(value))
    return value


def require_candidate_not_promoted(value: dict[str, Any]) -> None:
    if value.get("status") != "candidate-unclassified":
        raise DenominatorError("candidate status unexpectedly changed")
    if value.get("unclassified_count") != value.get("leaf_count"):
        raise DenominatorError("candidate unclassified count must equal leaf count")
    for field in ("sg1_eligible", "compatibility_credit", "production_ready"):
        if value.get(field) is not False:
            raise DenominatorError(f"candidate overclaim: {field}")


def license_class(data: bytes) -> str:
    head = data[:4096].decode("utf-8", errors="ignore").lower()
    if "apache license" in head and "licensed under" in head:
        return "apache-2.0"
    if "proprietary" in head or "strictly forbidden" in head or "all rights reserved" in head:
        return "restricted-review-required"
    return "unknown-review-required"


def strings_with_lines(text: str) -> Iterable[tuple[str, int]]:
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|`[^`]*`', re.DOTALL)
    for match in pattern.finditer(text):
        raw = match.group(0)
        if raw.startswith('"'):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            value = raw[1:-1]
        yield value, text.count("\n", 0, match.start()) + 1
