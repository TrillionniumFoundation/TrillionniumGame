#!/usr/bin/env python3
"""Apply the cumulative World PR127 module conversion to an exact checkout.

This driver is deliberately separate from workflow YAML. It applies the reviewed
nested-test, Campaign, RTS, and Game Server transforms, restores the historical
shared Game Server import namespace at the crate root, repairs the Rust 1.98.1
WebSocket-close lint, formats the workspace, and rebinds direct-source manifests.
It neither pushes World refs nor grants qualification or production authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True)


def rebind_manifest(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    source_root = path.parent.parent
    count = 0
    for field in ("parts", "nested_test_parts"):
        records = value.get(field, [])
        if not isinstance(records, list):
            raise RuntimeError(f"{path}: {field} is not a list")
        for record in records:
            target = source_root / record["path"]
            payload = target.read_bytes()
            record["bytes"] = len(payload)
            record["sha256"] = hashlib.sha256(payload).hexdigest()
            count += 1
    if count == 0:
        raise RuntimeError(f"{path}: no source records")
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def restore_game_server_root_imports(root: Path) -> None:
    crate = root / "trillionnium/crates/trnm-game-server/src"
    authority = crate / "lib_parts/authority_foundation/part_01.rs"
    library = crate / "lib.rs"

    authority_text = authority.read_text(encoding="utf-8", errors="strict")
    start = authority_text.index("use axum::extract::DefaultBodyLimit;")
    boundary = re.search(
        r"(?m)^(?:pub\(crate\)\s+)?const PLAYER_SESSION_HEADER",
        authority_text,
    )
    if boundary is None or boundary.start() <= start:
        raise RuntimeError("shared Game Server import declaration boundary drift")
    imports = authority_text[start : boundary.start()].rstrip() + "\n"
    for required in (
        "use uuid::Uuid;",
        "use tokio::sync::",
        "use axum::extract::DefaultBodyLimit;",
    ):
        if required not in imports:
            raise RuntimeError(f"shared Game Server import missing: {required}")
    authority.write_text(
        authority_text[:start] + authority_text[boundary.start() :],
        encoding="utf-8",
    )

    library_text = library.read_text(encoding="utf-8", errors="strict")
    anchor = "mod stream;\n"
    if library_text.count(anchor) != 1:
        raise RuntimeError("Game Server crate-root import insertion anchor drift")
    if "use axum::extract::DefaultBodyLimit;" in library_text:
        raise RuntimeError("shared imports already present at Game Server crate root")
    library.write_text(
        library_text.replace(anchor, anchor + "\n" + imports),
        encoding="utf-8",
    )


def stabilize_game_server_compatibility_boundary(root: Path) -> None:
    """Keep old root scope without weakening lint for unrelated imports.

    The extracted modules initially re-export the full historically shared root
    namespace so compile/test behavior remains stable. Some names are consumed
    only by particular targets or fault fixtures. The local allowance therefore
    applies only to generated ownership compatibility imports, never globally.
    """

    crate = root / "trillionnium/crates/trnm-game-server/src"
    library = crate / "lib.rs"
    text = library.read_text(encoding="utf-8", errors="strict")

    marker_replacements = {
        "// Ownership module: authority_foundation.": (
            "// Ownership section: authority_foundation. Migrated to an explicit Rust module."
        ),
        "// Ownership module: campaign_persistence.": (
            "// Ownership section: campaign_persistence. Migrated to an explicit Rust module."
        ),
    }
    for before, after in marker_replacements.items():
        if text.count(before) != 1:
            raise RuntimeError(f"ownership marker drift: {before}")
        text = text.replace(before, after)

    ownership_modules = (
        "authority_foundation",
        "configuration_and_migrations",
        "terminal_recovery",
        "operations_boundary",
        "fleet_fencing",
        "identity",
        "application",
        "http_routing",
        "readiness",
        "product_api",
        "actor_runtime",
        "campaign_persistence",
    )
    pattern = re.compile(
        r"(?m)^(use (?:" + "|".join(map(re.escape, ownership_modules)) + r")::)"
    )
    text, count = pattern.subn(r"#[allow(unused_imports)]\n\1", text)
    if count < 8:
        raise RuntimeError(
            f"unexpectedly small Game Server compatibility-import inventory: {count}"
        )
    library.write_text(text, encoding="utf-8")

    generated_module_files = (
        crate / "lib_parts/terminal_recovery/mod.rs",
        crate / "lib_parts/product_api/mod.rs",
        crate / "lib_parts/actor_runtime/mod.rs",
    )
    for path in generated_module_files:
        module_text = path.read_text(encoding="utf-8", errors="strict")
        module_text, module_count = re.subn(
            r"(?m)^(pub\(crate\) use part_[0-9]+::)",
            r"#[allow(unused_imports)]\n\1",
            module_text,
        )
        if module_count == 0:
            raise RuntimeError(f"missing compatibility re-export in {path}")
        path.write_text(module_text, encoding="utf-8")


def repair_websocket_close(root: Path) -> None:
    path = root / "trillionnium/crates/trnm-game-server/src/bin/trnm-online-e2e.rs"
    text = path.read_text(encoding="utf-8", errors="strict")
    old = "    let _ = tokio::task::block_in_place(|| state_stream.close(None));"
    new = (
        "    tokio::task::block_in_place(|| {\n"
        "        let _ = state_stream.close(None);\n"
        "    });"
    )
    if text.count(old) == 1 and new not in text:
        path.write_text(text.replace(old, new), encoding="utf-8")
    elif text.count(new) != 1 or old in text:
        raise RuntimeError("WebSocket close repair anchor drift")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--common-helper", type=Path, required=True)
    parser.add_argument("--stack-helper", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is None:
        raise RuntimeError("source SHA must be 40 lowercase hexadecimal characters")
    root = args.root.resolve(strict=True)
    common = args.common_helper.resolve(strict=True)
    stack = args.stack_helper.resolve(strict=True)

    run(
        sys.executable,
        str(common / "scripts/world-pr104-all-test-modules-transform.py"),
        str(root),
        "--source-sha",
        args.source_sha,
    )
    for helper in (
        "world-pr104-campaign-runtime-modules-transform.py",
        "world-pr104-rts-runtime-modules-transform.py",
    ):
        run(
            sys.executable,
            str(stack / "scripts" / helper),
            str(root),
            "--completed-commit",
            args.source_sha,
        )
    run(
        sys.executable,
        str(stack / "scripts/world-pr104-game-server-runtime-modules-transform.py"),
        str(root),
        "--completed-commit",
        args.source_sha,
        "--helper-root",
        str(stack / "scripts"),
    )

    restore_game_server_root_imports(root)
    stabilize_game_server_compatibility_boundary(root)
    repair_websocket_close(root)
    run(
        "cargo",
        "fmt",
        "--manifest-path",
        str(root / "trillionnium/Cargo.toml"),
        "--all",
    )
    for manifest in (
        root / "trillionnium/crates/trnm-campaign-core/src/lib_parts/manifest.json",
        root / "trillionnium/crates/trnm-rts-sim/src/lib_parts/manifest.json",
        root / "trillionnium/crates/trnm-game-server/src/lib_parts/manifest.json",
    ):
        rebind_manifest(manifest)

    print(
        "WORLD_PR127_CUMULATIVE_MODULE_TRANSFORM=PASS "
        "nested_test=10 campaign_runtime=10 rts_runtime=7 "
        "game_server_runtime=17 remaining_active=2 "
        "production_authorization=not_granted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
