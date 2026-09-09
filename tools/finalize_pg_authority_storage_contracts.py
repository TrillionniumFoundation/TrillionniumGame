#!/usr/bin/env python3
"""Synchronize AC-3 dependency and mandatory-test contracts from exact manifests."""
from __future__ import annotations

import argparse
import pprint
import re
import tomllib
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def synchronize_foundation_policy(root: Path) -> None:
    workspace = tomllib.loads(read(root / "Cargo.toml"))
    members = workspace.get("workspace", {}).get("members", [])
    require(isinstance(members, list) and members, "root workspace members missing")
    dependencies: dict[str, dict[str, Any]] = {}
    for member in members:
        require(isinstance(member, str), "workspace member must be a string")
        manifest_path = root / member / "Cargo.toml"
        require(manifest_path.is_file(), f"workspace manifest missing: {member}")
        manifest = tomllib.loads(read(manifest_path))
        value = manifest.get("dependencies", {})
        require(isinstance(value, dict), f"dependencies must be a table: {member}")
        dependencies[member] = value

    path = root / "scripts/check-rust-foundation.py"
    text = read(path)
    replacement = (
        "EXPECTED_DEPENDENCIES: dict[str, dict[str, Any]] = "
        + pprint.pformat(dependencies, width=100, sort_dicts=True)
        + "\n"
    )
    text, count = re.subn(
        r"(?s)EXPECTED_DEPENDENCIES: dict\[str, dict\[str, Any\]\] = .*?\n(?=FORBIDDEN_PURE_CORE_PATTERNS =)",
        replacement,
        text,
        count=1,
    )
    require(count == 1, "foundation dependency policy block missing")

    for test_name in (
        "authority_takeover_fences_stale_generation",
        "storage_occ_acl_and_batch_rollback_are_transactional",
    ):
        marker = f'    "{test_name}",\n'
        if marker not in text:
            anchor = '    "pgwire_commit_duplicate_conflict_and_fence_contract",\n'
            require(anchor in text, "foundation required-test anchor missing")
            text = text.replace(anchor, anchor + marker, 1)
    write(path, text)


def strengthen_server_source_contract(root: Path) -> None:
    path = root / "scripts/check-trnm-server.py"
    text = read(path)
    additions = {
        '    PERSISTENCE_ROOT / "authority.rs",\n': '    PERSISTENCE_ROOT / "auth.rs",\n',
        '    PERSISTENCE_ROOT / "storage.rs",\n': '    PERSISTENCE_ROOT / "session.rs",\n',
    }
    for line, anchor in additions.items():
        if line not in text:
            require(anchor in text, f"server required-file anchor missing: {anchor.strip()}")
            text = text.replace(anchor, anchor + line, 1)
    for test_name in (
        "authority_takeover_fences_stale_generation",
        "storage_occ_acl_and_batch_rollback_are_transactional",
    ):
        marker = f'    "{test_name}",\n'
        if marker not in text:
            anchor = '    "create_and_rotation_validation_fail_closed",\n'
            require(anchor in text, "server required-test anchor missing")
            text = text.replace(anchor, marker + anchor, 1)
    write(path, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    synchronize_foundation_policy(root)
    strengthen_server_source_contract(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
