#!/usr/bin/env python3
"""Prepare the bounded World Game Server nested-test module migration."""

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

    tests = root / "trillionnium/crates/trnm-game-server/src/lib_parts/tests"
    wrapper = tests / "part_01.rs"
    expected = '''use super::*;

struct FakeTerminalOrphanAuthority {
    recovered: Option<PublishedTickHighWater>,
    marker: Mutex<Option<PublishedTickHighWater>>,
    fail_acknowledgement: bool,
    durably_failed_closed: bool,
}

impl TerminalOrphanAuthority for FakeTerminalOrphanAuthority {
    async fn recover_running_high_water(
        &self,
        _high_water: &PublishedTickHighWater,
    ) -> Result<Option<PublishedTickHighWater>, String> {
        Ok(self.recovered.clone())
    }

    async fn acknowledge_terminal_high_water(
        &self,
        high_water: &PublishedTickHighWater,
    ) -> Result<bool, String> {
        if self.fail_acknowledgement {
            return Err("injected terminal marker failure".to_string());
        }
        *self.marker.lock().expect("fake marker lock") = Some(high_water.clone());
        Ok(true)
    }

    async fn reconcile_failed_closed_high_water(
        &self,
        _high_water: &PublishedTickHighWater,
    ) -> Result<bool, String> {
        Ok(self.durably_failed_closed)
    }
}

include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/game_server_tests_01.rs"
));
include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/game_server_tests_02.rs"
));
include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/game_server_tests_03.rs"
));
include!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/src/lib_parts/tests/game_server_tests_04.rs"
));'''
    if wrapper.read_text(encoding="utf-8") != expected:
        raise RuntimeError("Game Server test wrapper drift")

    prefix = expected[: expected.index("include!(concat!(")]
    modules = []
    for index in range(1, 5):
        modules.append(
            f'''#[path = "game_server_tests_{index:02d}.rs"]
mod game_server_tests_{index:02d};
#[allow(unused_imports)]
use game_server_tests_{index:02d}::*;
'''
        )
    wrapper.write_text(prefix + "\n".join(modules), encoding="utf-8")

    declaration = re.compile(
        r"^(    )(?:(pub(?:\([^)]*\))?\s+))?((?:async\s+)?fn|struct|enum|const|static|type|trait)\s+",
        re.MULTILINE,
    )
    for index in range(1, 5):
        path = tests / f"game_server_tests_{index:02d}.rs"
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
    for index in range(1, 5):
        expression = (
            f'concat!(env!("CARGO_MANIFEST_DIR"),'
            f'"/src/lib_parts/tests/game_server_tests_{index:02d}.rs")'
        )
        key = (
            "trillionnium/crates/trnm-game-server/src/lib_parts/tests/part_01.rs",
            expression,
        )
        if key in existing:
            raise RuntimeError(f"ledger entry already exists: {key}")
        ledger["completed"].append(
            {
                "path": key[0],
                "expression": expression,
                "replacement_module": f"game_server_tests_{index:02d}",
                "required_fragments": [
                    f'#[path = "game_server_tests_{index:02d}.rs"]',
                    f"mod game_server_tests_{index:02d};",
                ],
                "completed_commit": args.source_sha,
            }
        )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    manifest_path = root / "trillionnium/crates/trnm-game-server/src/lib_parts/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_base = manifest_path.parent.parent
    changed = {"lib_parts/tests/part_01.rs"} | {
        f"lib_parts/tests/game_server_tests_{index:02d}.rs" for index in range(1, 5)
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

    print("WORLD_GAME_SERVER_TEST_MODULE_TRANSFORM=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
