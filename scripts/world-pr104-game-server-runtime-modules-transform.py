#!/usr/bin/env python3
"""Convert the 17 remaining trnm-game-server crate-root runtime include seams.

The transform reuses the already qualified lexical/module helper used by the
Campaign and RTS tranches, preserves the existing crate-root public API, adds
only cross-module crate visibility that actual source references require, and
refreshes direct-source integrity after rustfmt.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


def load_base(script_root: Path):
    path = script_root / "world-pr104-rts-runtime-modules-transform.py"
    spec = importlib.util.spec_from_file_location("world_module_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module transform base: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--completed-commit", required=True)
    parser.add_argument("--helper-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    helper_root = args.helper_root.resolve(strict=True)
    base = load_base(helper_root)

    spec = importlib.util.spec_from_file_location(
        "include_engine", root / "scripts/_trnm_world_include_boundary_engine.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Rust source masker")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)

    crate = root / "trillionnium/crates/trnm-game-server/src"
    families = {
        "authority_foundation": ["part_01.rs"],
        "configuration_and_migrations": ["part_01.rs"],
        "terminal_recovery": [f"part_{index:02d}.rs" for index in range(1, 4)],
        "operations_boundary": ["part_01.rs"],
        "fleet_fencing": ["part_01.rs"],
        "identity": ["part_01.rs"],
        "application": ["part_01.rs"],
        "http_routing": ["part_01.rs"],
        "readiness": ["part_01.rs"],
        "product_api": [f"part_{index:02d}.rs" for index in range(1, 3)],
        "actor_runtime": [f"part_{index:02d}.rs" for index in range(1, 4)],
        "campaign_persistence": ["part_01.rs"],
    }
    exports: dict[str, tuple[list[str], list[str]]] = {}
    section_paths: dict[str, list[Path]] = {}
    changed: set[Path] = set()

    for section, filenames in families.items():
        section_public: list[str] = []
        section_internal: list[str] = []
        part_exports: list[tuple[str, list[str], list[str]]] = []
        paths: list[Path] = []
        for filename in filenames:
            path = crate / "lib_parts" / section / filename
            paths.append(path)
            public, internal = base.transform_part(path, engine.rust_code_mask)
            section_public.extend(public)
            section_internal.extend(internal)
            part_exports.append((filename, public, internal))
            changed.add(path)
        if len(section_public) != len(set(section_public)):
            raise RuntimeError(f"duplicate public name across {section}")
        if len(section_internal) != len(set(section_internal)):
            raise RuntimeError(f"duplicate private name across {section}")
        exports[section] = (section_public, section_internal)
        section_paths[section] = paths

        if len(filenames) > 1:
            module_path = crate / "lib_parts" / section / "mod.rs"
            chunks = ["use super::*;\n\n"]
            for filename, public, internal in part_exports:
                module = Path(filename).stem
                chunks.append(f"mod {module};\n")
                chunks.append(base.format_use("pub(crate) use", module, internal))
                chunks.append(base.format_use("pub use", module, public))
                chunks.append("\n")
            module_path.write_text("".join(chunks), encoding="utf-8")
            changed.add(module_path)

    raw_by_path = {
        path: path.read_text(encoding="utf-8", errors="strict")
        for path in crate.rglob("*.rs")
    }
    masked_by_path = {
        path: engine.rust_code_mask(text)
        for path, text in raw_by_path.items()
    }
    for section, (public, internal) in list(exports.items()):
        excluded = set(section_paths[section])
        excluded.add(crate / "lib_parts" / section / "mod.rs")
        outside_masked = [text for path, text in masked_by_path.items() if path not in excluded]
        outside_raw = [text for path, text in raw_by_path.items() if path not in excluded]
        exports[section] = (
            public,
            [
                name for name in internal
                if base.word_used(name, outside_masked)
                or base.macro_attribute_used(name, outside_raw)
            ],
        )

    lib = crate / "lib.rs"
    source = lib.read_text(encoding="utf-8", errors="strict")
    start_marker = "// Ownership section: authority_foundation. Ordinary Git-tracked source.\n"
    end_marker = "// Ownership section: tests. Explicit ordinary Rust module."
    start = source.index(start_marker)
    end = source.index(end_marker)
    blocks: list[str] = []
    for section, filenames in families.items():
        public, internal = exports[section]
        block = (
            base.multipart_module(section, public, internal)
            if len(filenames) > 1
            else base.single_module(section, public, internal)
        )
        blocks.extend((block, "\n"))
    lib.write_text(source[:start] + "".join(blocks) + source[end:], encoding="utf-8")
    changed.add(lib)

    ledger_path = root / "scripts/contracts/trnm-world-include-migrations-v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    existing = {(item["path"], item["expression"]) for item in ledger["completed"]}
    for section, filenames in families.items():
        replacement = "mod.rs" if len(filenames) > 1 else filenames[0]
        for filename in filenames:
            expression = f'"lib_parts/{section}/{filename}"'
            key = ("trillionnium/crates/trnm-game-server/src/lib.rs", expression)
            if key in existing:
                raise RuntimeError(f"already completed: {key}")
            ledger["completed"].append(
                {
                    "path": key[0],
                    "expression": expression,
                    "replacement_module": section,
                    "required_fragments": [
                        f'#[path = "lib_parts/{section}/{replacement}"]',
                        f"mod {section};",
                    ],
                    "completed_commit": args.completed_commit,
                }
            )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    manifest_path = crate / "lib_parts/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for section in ("terminal_recovery", "product_api", "actor_runtime"):
        new_relative = f"lib_parts/{section}/mod.rs"
        if any(record["path"] == new_relative for record in manifest["parts"]):
            raise RuntimeError(f"{new_relative} already catalogued")
        manifest["parts"].append(
            {"bytes": 0, "path": new_relative, "section": section, "sha256": ""}
        )

    subprocess.run(
        ["cargo", "fmt", "--manifest-path", str(root / "trillionnium/Cargo.toml"), "--all"],
        check=True,
    )

    records = {record["path"]: record for record in manifest["parts"]}
    for path in changed:
        try:
            relative = path.relative_to(crate).as_posix()
        except ValueError:
            continue
        record = records.get(relative)
        if record is None:
            continue
        payload = path.read_bytes()
        record["bytes"] = len(payload)
        record["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest["parts"] = sorted(
        manifest["parts"],
        key=lambda record: (manifest["sections"].index(record["section"]), record["path"]),
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("WORLD_GAME_SERVER_RUNTIME_MODULE_TRANSFORM=PASS seams=17")
    for section, (public, internal) in exports.items():
        print(f"{section}: public={len(public)} cross_module_internal={len(internal)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
