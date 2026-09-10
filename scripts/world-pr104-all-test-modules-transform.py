#!/usr/bin/env python3
"""Prepare a bounded migration of all remaining active nested-test include! seams.

The transformation is mechanical and fail-closed. It changes three existing test
wrappers, their ten test-body files, the include migration ledger, one
Clippy-equivalent expression and the three direct-source manifests. The caller
must still run rustfmt, all-target tests, strict Clippy and the World truth/source
qualification suites before proposing the result upstream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Family:
    name: str
    wrapper: str
    children: tuple[str, ...]
    manifest: str


FAMILIES = (
    Family(
        name="campaign",
        wrapper="trillionnium/crates/trnm-campaign-core/src/lib_parts/tests/part_01.rs",
        children=("campaign_tests_01.rs", "campaign_tests_02.rs", "campaign_tests_03.rs"),
        manifest="trillionnium/crates/trnm-campaign-core/src/lib_parts/manifest.json",
    ),
    Family(
        name="rts",
        wrapper="trillionnium/crates/trnm-rts-sim/src/lib_parts/tests/part_01.rs",
        children=("rts_tests_01.rs", "rts_tests_02.rs", "rts_tests_03.rs"),
        manifest="trillionnium/crates/trnm-rts-sim/src/lib_parts/manifest.json",
    ),
    Family(
        name="game_server",
        wrapper="trillionnium/crates/trnm-game-server/src/lib_parts/tests/part_01.rs",
        children=(
            "game_server_tests_01.rs",
            "game_server_tests_02.rs",
            "game_server_tests_03.rs",
            "game_server_tests_04.rs",
        ),
        manifest="trillionnium/crates/trnm-game-server/src/lib_parts/manifest.json",
    ),
)

# These source bodies are indented by one level because they were textually
# included in `mod tests`. Nested declarations are indented further and are not
# widened. Test-only `pub(super)` cannot alter the public crate API.
TOP_LEVEL_DECLARATION = re.compile(
    r"^(    )(?:(pub(?:\([^)]*\))?\s+))?"
    r"((?:async\s+)?fn|struct|enum|const|static|type|trait|mod)\s+",
    re.MULTILINE,
)


def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise RuntimeError(f"non-finite JSON constant: {value}")


def load_json(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8", errors="strict"),
        object_pairs_hook=strict_object,
        parse_constant=reject_constant,
    )


def include_expression(child: str) -> str:
    return f'concat!(env!("CARGO_MANIFEST_DIR"),"/src/lib_parts/tests/{child}")'


def include_block(child: str) -> str:
    return (
        "include!(concat!(\n"
        "    env!(\"CARGO_MANIFEST_DIR\"),\n"
        f"    \"/src/lib_parts/tests/{child}\"\n"
        "));"
    )


def module_block(child: str) -> str:
    module = child.removesuffix(".rs")
    return (
        f'#[path = "{child}"]\n'
        f"mod {module};\n"
        "#[allow(unused_imports)]\n"
        f"use {module}::*;"
    )


def transform_wrapper(path: Path, family: Family) -> None:
    source = path.read_text(encoding="utf-8", errors="strict").rstrip()
    expected_tail = "\n".join(include_block(child) for child in family.children)
    if source.count("include!(") != len(family.children):
        raise RuntimeError(f"{family.name} wrapper include count drift")
    if not source.endswith(expected_tail):
        raise RuntimeError(f"{family.name} wrapper include tail drift")
    prefix = source[: -len(expected_tail)].rstrip()
    if not prefix.startswith("use super::*;"):
        raise RuntimeError(f"{family.name} wrapper prelude drift")
    replacement = prefix + "\n\n" + "\n\n".join(
        module_block(child) for child in family.children
    )
    path.write_text(replacement + "\n", encoding="utf-8")


def transform_child(path: Path) -> None:
    source = path.read_text(encoding="utf-8", errors="strict")
    if source.startswith("use super::*;"):
        raise RuntimeError(f"already transformed: {path}")
    transformed, count = TOP_LEVEL_DECLARATION.subn(
        lambda match: f"{match.group(1)}pub(super) {match.group(3)} ",
        source,
    )
    if count == 0:
        raise RuntimeError(f"no top-level declaration found: {path}")
    path.write_text("use super::*;\n\n" + transformed, encoding="utf-8")


def update_manifest(root: Path, family: Family) -> None:
    manifest_path = root / family.manifest
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RuntimeError(f"manifest root is not an object: {family.manifest}")
    source_root = manifest_path.parent.parent
    wrapper_absolute = root / family.wrapper
    wrapper_relative = wrapper_absolute.relative_to(source_root).as_posix()
    changed = {wrapper_relative}
    changed.update(
        wrapper_absolute.parent.joinpath(child).relative_to(source_root).as_posix()
        for child in family.children
    )
    seen: set[str] = set()
    for field in ("parts", "nested_test_parts"):
        records = manifest.get(field)
        if not isinstance(records, list):
            raise RuntimeError(f"{family.manifest}: missing {field}")
        for record in records:
            if not isinstance(record, dict):
                raise RuntimeError(f"{family.manifest}: malformed {field} entry")
            relative = record.get("path")
            if isinstance(relative, str) and relative in changed:
                payload = (source_root / relative).read_bytes()
                record["bytes"] = len(payload)
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                seen.add(relative)
    if seen != changed:
        raise RuntimeError(
            f"{family.manifest}: coverage drift; seen={sorted(seen)} expected={sorted(changed)}"
        )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is None:
        raise RuntimeError("source SHA must be 40 lowercase hexadecimal characters")
    root = args.root.resolve(strict=True)

    p0 = root / "trillionnium/crates/trnm-game-server/tests/p0_boundary_contract.rs"
    source = p0.read_text(encoding="utf-8", errors="strict")
    old = ".rfind(|character| matches!(character, ';' | '{' | '}'))"
    new = ".rfind([';', '{', '}'])"
    if source.count(old) == 1 and new not in source:
        p0.write_text(source.replace(old, new), encoding="utf-8")
    elif source.count(new) != 1 or old in source:
        raise RuntimeError("P0 boundary expression drift")

    ledger_path = root / "scripts/contracts/trnm-world-include-migrations-v1.json"
    ledger = load_json(ledger_path)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("completed"), list):
        raise RuntimeError("include migration ledger shape drift")
    existing = {
        (item.get("path"), item.get("expression"))
        for item in ledger["completed"]
        if isinstance(item, dict)
    }

    migrated = 0
    for family in FAMILIES:
        wrapper = root / family.wrapper
        transform_wrapper(wrapper, family)
        for child in family.children:
            transform_child(wrapper.parent / child)
            expression = include_expression(child)
            key = (family.wrapper, expression)
            if key in existing:
                raise RuntimeError(f"ledger entry already exists: {key}")
            module = child.removesuffix(".rs")
            ledger["completed"].append(
                {
                    "path": family.wrapper,
                    "expression": expression,
                    "replacement_module": module,
                    "required_fragments": [
                        f'#[path = "{child}"]',
                        f"mod {module};",
                    ],
                    "completed_commit": args.source_sha,
                }
            )
            existing.add(key)
            migrated += 1

    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    for family in FAMILIES:
        update_manifest(root, family)

    if migrated != 10:
        raise RuntimeError(f"expected ten migrated seams, observed {migrated}")
    print("WORLD_ALL_ACTIVE_NESTED_TEST_MODULE_TRANSFORM=PASS seams=10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
