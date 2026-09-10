#!/usr/bin/env python3
"""Prepare a bounded migration of all remaining active nested test include! seams.

The transformation is intentionally mechanical and fail-closed. It changes only
three existing test wrappers, their ten test-body files, the include migration
ledger, one Clippy-equivalent expression, and the three direct-source manifests.
The caller must run rustfmt, all-target tests, strict Clippy and the World truth /
source qualification suites before the result can be proposed upstream.
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
    prelude: str


FAMILIES = (
    Family(
        name="campaign",
        wrapper="trillionnium/crates/trnm-campaign-core/src/lib_parts/tests/part_01.rs",
        children=("campaign_tests_01.rs", "campaign_tests_02.rs", "campaign_tests_03.rs"),
        manifest="trillionnium/crates/trnm-campaign-core/src/lib_parts/manifest.json",
        prelude="use super::*;\nuse tempfile::tempdir;\n",
    ),
    Family(
        name="rts",
        wrapper="trillionnium/crates/trnm-rts-sim/src/lib_parts/tests/part_01.rs",
        children=("rts_tests_01.rs", "rts_tests_02.rs", "rts_tests_03.rs"),
        manifest="trillionnium/crates/trnm-rts-sim/src/lib_parts/manifest.json",
        prelude=(
            "use super::*;\n"
            "use std::{fs, path::PathBuf};\n"
            "use tempfile::tempdir;\n"
            "use trnm_campaign_core::{\n"
            "    BattleMapNodeV1, BattleMapSeedV1, CampaignMission, CampaignRoom, CampaignSaveV1,\n"
            "    MissionDefinition, QuestState,\n"
            "};\n"
            "use trnm_rts_protocol::{RtsOrderSource, RtsTile};\n"
        ),
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
        prelude="use super::*;\n",
    ),
)

# The source bodies are indented by exactly one level because they were formerly
# textually included in `mod tests`. Nested declarations are indented further and
# must not be widened. Test-only visibility cannot affect the public crate API.
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


def load_json(path: Path) -> object:
    return json.loads(
        path.read_text(encoding="utf-8", errors="strict"),
        object_pairs_hook=strict_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            RuntimeError(f"non-finite JSON constant: {value}")
        ),
    )


def include_expression(crate_relative: str, child: str) -> str:
    return f'concat!(env!("CARGO_MANIFEST_DIR"),"/{crate_relative}/{child}")'


def expected_wrapper(family: Family) -> str:
    wrapper = Path(family.wrapper)
    crate_root = wrapper.parts[: wrapper.parts.index("src") + 1]
    body_relative = "/".join((*crate_root[-1:], *wrapper.parent.relative_to(Path(*crate_root)).parts))
    # body_relative is `src/lib_parts/tests` for all currently selected families.
    blocks = []
    for child in family.children:
        blocks.append(
            "include!(concat!(\n"
            "    env!(\"CARGO_MANIFEST_DIR\"),\n"
            f"    \"/{body_relative}/{child}\"\n"
            "));"
        )
    return family.prelude + "\n" + "\n".join(blocks)


def replacement_wrapper(family: Family) -> str:
    blocks = []
    for child in family.children:
        module = child.removesuffix(".rs")
        blocks.append(
            f'#[path = "{child}"]\n'
            f"mod {module};\n"
            "#[allow(unused_imports)]\n"
            f"use {module}::*;"
        )
    return family.prelude + "\n" + "\n\n".join(blocks)


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
    wrapper_relative = str(Path(family.wrapper).relative_to(source_root))
    changed = {wrapper_relative}
    changed.update(
        str(Path(family.wrapper).parent.joinpath(child).relative_to(source_root))
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
            if relative in changed:
                payload = (source_root / str(relative)).read_bytes()
                record["bytes"] = len(payload)
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                seen.add(str(relative))
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
        current = wrapper.read_text(encoding="utf-8", errors="strict").rstrip()
        expected = expected_wrapper(family).rstrip()
        if current != expected:
            raise RuntimeError(f"{family.name} test wrapper drift")
        wrapper.write_text(replacement_wrapper(family).rstrip() + "\n", encoding="utf-8")

        crate_relative = str(Path(family.wrapper).parent.relative_to(
            Path(family.wrapper).parents[len(Path(family.wrapper).parts) - Path(family.wrapper).parts.index("src") - 2]
        ))
        # Avoid depending on the calculation above for the normative expression.
        crate_relative = "src/lib_parts/tests"
        for child in family.children:
            child_path = wrapper.parent / child
            transform_child(child_path)
            expression = include_expression(crate_relative, child)
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
