#!/usr/bin/env python3
"""Prepare the bounded World RTS nested-test module migration."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label} anchor count drift: {count}")
    return text.replace(old, new)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = args.root.resolve()

    p0 = root / "trillionnium/crates/trnm-game-server/tests/p0_boundary_contract.rs"
    p0_source = p0.read_text(encoding="utf-8")
    p0.write_text(
        replace_once(
            p0_source,
            ".rfind(|character| matches!(character, ';' | '{' | '}'))",
            ".rfind([';', '{', '}'])",
            "P0 lint repair",
        ),
        encoding="utf-8",
    )

    tests = root / "trillionnium/crates/trnm-rts-sim/src/lib_parts/tests"
    wrapper = tests / "part_01.rs"
    expected = '''use super::*;
use std::{fs, path::PathBuf};
use tempfile::tempdir;
use trnm_campaign_core::{
    BattleMapNodeV1, BattleMapSeedV1, CampaignMission, CampaignRoom, CampaignSaveV1,
    MissionDefinition, QuestState,
};
use trnm_rts_protocol::{RtsOrderSource, RtsTile};

include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/rts_tests_01.rs"
));
include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/rts_tests_02.rs"
));
include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/rts_tests_03.rs"
));
'''
    if wrapper.read_text(encoding="utf-8") != expected:
        raise RuntimeError("RTS test wrapper drift")
    wrapper.write_text(
        '''use super::*;
use std::{fs, path::PathBuf};
use tempfile::tempdir;
use trnm_campaign_core::{
    BattleMapNodeV1, BattleMapSeedV1, CampaignMission, CampaignRoom, CampaignSaveV1,
    MissionDefinition, QuestState,
};
use trnm_rts_protocol::{RtsOrderSource, RtsTile};

#[path = "rts_tests_01.rs"]
mod rts_tests_01;
#[allow(unused_imports)]
use rts_tests_01::*;

#[path = "rts_tests_02.rs"]
mod rts_tests_02;
#[allow(unused_imports)]
use rts_tests_02::*;

#[path = "rts_tests_03.rs"]
mod rts_tests_03;
#[allow(unused_imports)]
use rts_tests_03::*;
''',
        encoding="utf-8",
    )

    declaration = re.compile(
        r"^(    )(?:(pub(?:\([^)]*\))?\s+))?((?:async\s+)?fn|struct|enum|const|static|type|trait)\s+",
        re.MULTILINE,
    )
    for index in range(1, 4):
        path = tests / f"rts_tests_{index:02d}.rs"
        source = path.read_text(encoding="utf-8")
        if source.startswith("use super::*;"):
            raise RuntimeError(f"already transformed: {path}")
        source = declaration.sub(
            lambda match: f"{match.group(1)}pub(super) {match.group(3)} ",
            source,
        )
        path.write_text("use super::*;\n\n" + source, encoding="utf-8")

    ledger_path = root / "scripts/contracts/trnm-world-include-migrations-v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    existing = {(item["path"], item["expression"]) for item in ledger["completed"]}
    for index in range(1, 4):
        expression = (
            f'concat!(env!("CARGO_MANIFEST_DIR"),'
            f'"/src/lib_parts/tests/rts_tests_{index:02d}.rs")'
        )
        key = (
            "trillionnium/crates/trnm-rts-sim/src/lib_parts/tests/part_01.rs",
            expression,
        )
        if key in existing:
            raise RuntimeError(f"ledger entry already exists: {key}")
        ledger["completed"].append(
            {
                "path": key[0],
                "expression": expression,
                "replacement_module": f"rts_tests_{index:02d}",
                "required_fragments": [
                    f'#[path = "rts_tests_{index:02d}.rs"]',
                    f"mod rts_tests_{index:02d};",
                ],
                "completed_commit": args.source_sha,
            }
        )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    manifest_path = root / "trillionnium/crates/trnm-rts-sim/src/lib_parts/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_base = manifest_path.parent.parent
    changed = {
        "lib_parts/tests/part_01.rs",
        "lib_parts/tests/rts_tests_01.rs",
        "lib_parts/tests/rts_tests_02.rs",
        "lib_parts/tests/rts_tests_03.rs",
    }
    seen: set[str] = set()
    for field in ("parts", "nested_test_parts"):
        for record in manifest[field]:
            if record["path"] in changed:
                payload = (manifest_base / record["path"]).read_bytes()
                record["bytes"] = len(payload)
                record["sha256"] = hashlib.sha256(payload).hexdigest()
                seen.add(record["path"])
    if seen != changed:
        raise RuntimeError(f"manifest coverage drift: seen={sorted(seen)} expected={sorted(changed)}")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("WORLD_RTS_TEST_MODULE_TRANSFORM=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
