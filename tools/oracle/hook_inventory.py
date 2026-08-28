from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMPORT_RE = re.compile(r'^\s*(?:(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s+)?"(?P<path>[^"]+)"\s*$')
CALL_RE = re.compile(r"(?P<call>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
VERSION_SEGMENT_RE = re.compile(r"^v[0-9]+$")


class InventoryError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def git_blob_sha(value: bytes) -> str:
    return hashlib.sha1(f"blob {len(value)}\0".encode("ascii") + value, usedforsecurity=False).hexdigest()


def stable_id(capability: str, path: str, line: int, column: int, call: str) -> str:
    seed = f"{capability}\0{path}\0{line}\0{column}\0{call}".encode("utf-8")
    return "TG-ORACLE-HOOK-" + hashlib.sha256(seed).hexdigest()[:20].upper()


def _run(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise InventoryError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def verify_upstream(root: Path, policy: Mapping[str, Any]) -> dict[str, str]:
    upstream = policy.get("upstream")
    if not isinstance(upstream, dict):
        raise InventoryError("policy.upstream must be an object")
    expected_repository = upstream.get("repository")
    expected_commit = upstream.get("commit")
    expected_tree = upstream.get("tree")
    if expected_repository != "heroiclabs/nakama" or not isinstance(expected_commit, str) or not SHA_RE.fullmatch(expected_commit):
        raise InventoryError("policy upstream repository/commit is invalid")
    if not isinstance(expected_tree, str) or not SHA_RE.fullmatch(expected_tree):
        raise InventoryError("policy upstream tree is invalid")
    actual_commit = _run(root, "rev-parse", "HEAD")
    actual_tree = _run(root, "rev-parse", "HEAD^{tree}")
    if actual_commit != expected_commit or actual_tree != expected_tree:
        raise InventoryError(
            f"upstream identity mismatch: expected {expected_commit}/{expected_tree}, got {actual_commit}/{actual_tree}"
        )
    return {"repository": expected_repository, "commit": actual_commit, "tree": actual_tree}


def _default_import_alias(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ""
    if VERSION_SEGMENT_RE.fullmatch(parts[-1]) and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def import_aliases(source: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped == "import (":
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        candidate = stripped
        if stripped.startswith("import "):
            candidate = stripped.removeprefix("import ").strip()
        elif not in_block:
            continue
        match = IMPORT_RE.fullmatch(candidate)
        if not match:
            continue
        path = match.group("path")
        alias = match.group("alias") or _default_import_alias(path)
        if alias not in {"_", ".", ""}:
            aliases[alias] = path
    return aliases


def _strip_line_comments(line: str, in_block: bool) -> tuple[str, bool]:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(line):
        if in_block:
            end = line.find("*/", index)
            if end < 0:
                return "".join(output), True
            in_block = False
            index = end + 2
            continue
        character = line[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue
        if line.startswith("//", index):
            break
        if line.startswith("/*", index):
            in_block = True
            index += 2
            continue
        output.append(character)
        if character == '"':
            in_string = True
        index += 1
    return "".join(output), in_block


def _capability(call: str, line: str, policy: Mapping[str, Any]) -> str | None:
    capabilities = policy.get("capabilities")
    if not isinstance(capabilities, dict):
        raise InventoryError("policy.capabilities must be an object")
    for capability, markers in capabilities.items():
        if not isinstance(markers, list):
            raise InventoryError(f"capability {capability} markers must be a list")
        for marker in markers:
            if not isinstance(marker, str):
                raise InventoryError(f"capability {capability} marker must be a string")
            if marker.startswith(".") and call.endswith(marker):
                return capability
            if marker in call or marker in line:
                return capability
    return None


def _listed_go_files(root: Path, include_roots: Iterable[str]) -> list[str]:
    paths: set[str] = set()
    for include_root in include_roots:
        output = _run(root, "ls-files", f"{include_root}/**/*.go", f"{include_root}/*.go")
        paths.update(line for line in output.splitlines() if line and not line.endswith("_test.go"))
    return sorted(paths)


def generate_inventory(root: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    upstream = verify_upstream(root, policy)
    include_roots = policy.get("include_roots")
    if not isinstance(include_roots, list) or not include_roots:
        raise InventoryError("policy.include_roots must be a non-empty list")
    restricted = set(policy.get("restricted_paths", []))
    sites: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []

    for relative in _listed_go_files(root, include_roots):
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise InventoryError(f"unsafe source path: {relative}")
        data = (root / relative).read_bytes()
        blob = git_blob_sha(data)
        source_digest = sha256(data)
        if relative in restricted:
            manual.append(
                {
                    "class": "restricted_source_manual_contract",
                    "path": relative,
                    "git_blob": blob,
                    "source_sha256": source_digest,
                    "reason": "restricted source is inventoried by identity only and is not semantically scanned",
                }
            )
            continue
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            manual.append(
                {
                    "class": "non_utf8_source_manual_contract",
                    "path": relative,
                    "git_blob": blob,
                    "source_sha256": source_digest,
                    "reason": str(exc),
                }
            )
            continue
        aliases = import_aliases(source)
        in_block = False
        for line_number, raw_line in enumerate(source.splitlines(), start=1):
            code, in_block = _strip_line_comments(raw_line, in_block)
            for match in CALL_RE.finditer(code):
                call = match.group("call")
                capability = _capability(call, code, policy)
                if capability is None:
                    continue
                root_alias = call.split(".", 1)[0]
                site = {
                    "id": stable_id(capability, relative, line_number, match.start() + 1, call),
                    "capability": capability,
                    "path": relative,
                    "line": line_number,
                    "column": match.start() + 1,
                    "call": call,
                    "import_path": aliases.get(root_alias),
                    "git_blob": blob,
                    "source_sha256": source_digest,
                    "review_state": "candidate-unreviewed",
                    "patch_authorized": False,
                }
                sites.append(site)

    sites.sort(key=lambda item: (item["path"], item["line"], item["column"], item["capability"], item["call"]))
    ids = [item["id"] for item in sites]
    if len(ids) != len(set(ids)):
        raise InventoryError("stable instrumentation site IDs are not unique")
    counts: dict[str, int] = {}
    for site in sites:
        counts[site["capability"]] = counts.get(site["capability"], 0) + 1
    result = {
        "schema": "trillionnium.oracle-instrumentation-site-inventory.v1",
        "project_id": "trillionnium-game",
        "status": "candidate-unreviewed",
        "upstream": upstream,
        "site_count": len(sites),
        "capability_counts": dict(sorted(counts.items())),
        "sites": sites,
        "manual_contracts": sorted(manual, key=lambda item: item["path"]),
        "claims": {
            "patch_generated": False,
            "patch_authorized": False,
            "instrumented_image_built": False,
            "instrumented_equivalence": False,
            "sg2_complete": False,
            "compatibility_credit": False,
            "production_ready": False,
        },
    }
    result["content_sha256"] = sha256(canonical_bytes(result))
    return result
