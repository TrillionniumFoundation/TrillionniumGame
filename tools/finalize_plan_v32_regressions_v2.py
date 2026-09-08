#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(read(path))
    require(isinstance(value, dict), f"{path}: object required")
    return value


def dump(path: Path, value: dict[str, Any]) -> None:
    write(path, json.dumps(value, indent=2, ensure_ascii=False))


def blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def update_overlay(root: Path) -> None:
    base_path = root / "docs/governance/REQUIRED_WORKFLOWS_V1.json"
    overlay_path = root / "docs/governance/REQUIRED_WORKFLOWS_OVERLAY_V1.json"
    base = load(base_path)
    overlay = load(overlay_path)
    overlay["base_manifest_blob_sha1"] = blob_sha1(base_path)
    for field in ("replace_workflows", "add_workflows"):
        rows = overlay.get(field)
        require(isinstance(rows, list), f"overlay {field} must be a list")
        for row in rows:
            require(isinstance(row, dict), f"overlay {field} row must be object")
            relative = row.get("path")
            require(isinstance(relative, str), f"overlay {field} path missing")
            workflow = root / relative
            require(workflow.is_file(), f"overlay workflow missing: {relative}")
            row["git_blob_sha1"] = blob_sha1(workflow)
    removals = overlay.get("remove_workflow_ids", [])
    additions = overlay.get("add_workflows", [])
    base_rows = base.get("workflows", [])
    require(isinstance(removals, list) and isinstance(additions, list) and isinstance(base_rows, list), "workflow arrays invalid")
    overlay["composed_external_workflow_count"] = len(base_rows) - len(removals) + len(additions)
    payload = dict(overlay)
    payload.pop("overlay_sha256", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    overlay["overlay_sha256"] = hashlib.sha256(encoded).hexdigest()
    dump(overlay_path, overlay)


def update_server_tests(root: Path) -> None:
    path = root / "tests/control_plane/test_rust_server_slice.py"
    text = read(path)
    text = text.replace(
        'self.assertFalse(result["authority_transferred"])',
        'self.assertTrue(result["authority_transferred_source_candidate"])',
    )
    text = text.replace(
        '"crates/trnm-persistence-pg/src/bin/trnm-server.rs",',
        '"crates/trnm-server/src/main.rs",',
        1,
    )
    text = text.replace(
        'self.assertEqual(result["candidate_server"], "crates/trnm-server/src/main.rs")',
        'self.assertEqual(result["diagnostic_server"], "crates/trnm-persistence-pg/src/bin/trnm-server.rs")',
    )
    text = text.replace(
        'self.assertEqual(result["binary"], "trnm-server-composition-candidate")',
        'self.assertEqual(result["binary"], "trnm-server")',
    )
    write(path, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    update_overlay(root)
    update_server_tests(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
