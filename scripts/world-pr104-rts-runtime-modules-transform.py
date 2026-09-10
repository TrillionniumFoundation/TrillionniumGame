#!/usr/bin/env python3
"""Convert all remaining trnm-rts-sim runtime include seams to modules."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

IDENT = r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*"
VIS = r"(?P<vis>pub(?:\([^)]*\))?\s+)?"
FN = re.compile(
    rf"^(?P<indent>\s*){VIS}(?P<prefix>(?:(?:unsafe|async|const)\s+|extern\s+\"[^\"]+\"\s+)*)fn\s+(?P<name>{IDENT})"
)
ITEM = re.compile(
    rf"^(?P<indent>\s*){VIS}(?P<kind>struct|enum|union|trait|type|const|static)\s+(?P<name>{IDENT})"
)
IMPL = re.compile(r"^\s*(?:unsafe\s+)?impl\b")
FIELD = re.compile(
    rf"^(?P<indent>\s*)(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<name>{IDENT})\s*:"
)
ASSOCIATED = re.compile(
    rf"^(?P<indent>\s*){VIS}(?P<kind>type|const)\s+(?P<name>{IDENT})"
)
ATTRIBUTE = re.compile(r"#\s*\[[^\]]*\]", re.DOTALL)


def add_crate_visibility(line: str, match: re.Match[str]) -> str:
    if match.groupdict().get("vis"):
        return line
    offset = len(match.group("indent"))
    return line[:offset] + "pub(crate) " + line[offset:]


def classify_impl(header: str) -> str:
    return "trait_impl" if re.search(r"\bfor\b", header.split("{", 1)[0]) else "impl"


def transform_part(path: Path, mask_fn) -> tuple[list[str], list[str]]:
    raw = path.read_text(encoding="utf-8", errors="strict")
    if raw.startswith("use super::*;\n"):
        raise RuntimeError(f"already transformed: {path}")
    masked = mask_fn(raw)
    source_lines = raw.splitlines(keepends=True)
    mask_lines = masked.splitlines(keepends=True)
    if len(source_lines) != len(mask_lines):
        raise RuntimeError(f"mask line drift: {path}")

    depth = 0
    pending: str | None = None
    pending_impl = ""
    outer: str | None = None
    public: list[str] = []
    private: list[str] = []
    output: list[str] = []

    for line, code in zip(source_lines, mask_lines):
        if depth == 0:
            if pending == "impl_pending":
                pending_impl += code
            elif IMPL.match(code):
                pending = "impl_pending"
                pending_impl = code
            else:
                match = FN.match(code) or ITEM.match(code)
                if match:
                    kind = "fn" if FN.match(code) else match.group("kind")
                    name = match.group("name")
                    if match.groupdict().get("vis") == "pub ":
                        public.append(name)
                    else:
                        private.append(name)
                        line = add_crate_visibility(line, match)
                    pending = kind
        elif depth == 1 and outer == "struct":
            match = FIELD.match(code)
            if match and not match.groupdict().get("vis"):
                line = add_crate_visibility(line, match)
        elif depth == 1 and outer == "impl":
            match = FN.match(code) or ASSOCIATED.match(code)
            if match and not match.groupdict().get("vis"):
                line = add_crate_visibility(line, match)

        output.append(line)
        opens = code.count("{")
        closes = code.count("}")
        before = depth
        depth += opens - closes
        if before == 0 and opens:
            outer = classify_impl(pending_impl) if pending == "impl_pending" else pending
            pending = None
            pending_impl = ""
        if depth == 0:
            outer = None
            if ";" in code and opens == 0:
                pending = None
                pending_impl = ""

    if depth != 0:
        raise RuntimeError(f"unbalanced braces: {path}: {depth}")
    if len(public) != len(set(public)) or len(private) != len(set(private)):
        raise RuntimeError(f"duplicate top-level item: {path}")
    path.write_text("use super::*;\n\n" + "".join(output), encoding="utf-8")
    return public, private


def format_use(prefix: str, module: str, names: list[str]) -> str:
    if not names:
        return ""
    values = sorted(names)
    if len(values) <= 3 and sum(map(len, values)) < 72:
        return f"{prefix} {module}::{{{', '.join(values)}}};\n"
    return f"{prefix} {module}::{{\n" + "".join(f"    {name},\n" for name in values) + "};\n"


def single_module(section: str, public: list[str], private: list[str]) -> str:
    return (
        f"// Ownership module: {section}. Ordinary Git-tracked Rust module.\n"
        f"#[path = \"lib_parts/{section}/part_01.rs\"]\n"
        f"mod {section};\n"
        + format_use("use", section, private)
        + format_use("pub use", section, public)
    )


def multipart_module(section: str, public: list[str], private: list[str]) -> str:
    return (
        f"// Ownership module: {section}. Multi-part implementation is isolated\n"
        f"// behind one module with explicit crate-private cross-part visibility.\n"
        f"#[path = \"lib_parts/{section}/mod.rs\"]\n"
        f"mod {section};\n"
        + format_use("use", section, private)
        + format_use("pub use", section, public)
    )


def word_used(name: str, texts: list[str]) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    return any(pattern.search(text) for text in texts)


def attribute_used(name: str, texts: list[str]) -> bool:
    quoted = re.compile(rf'"(?:{IDENT}::)*{re.escape(name)}"')
    return any(
        quoted.search(match.group(0))
        for text in texts
        for match in ATTRIBUTE.finditer(text)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--completed-commit", required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)

    spec = importlib.util.spec_from_file_location(
        "include_engine", root / "scripts/_trnm_world_include_boundary_engine.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Rust source masker")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)

    crate = root / "trillionnium/crates/trnm-rts-sim/src"
    families = {
        "contracts_and_primitives": ["part_01.rs"],
        "mission_runtime": [f"part_{index:02d}.rs" for index in range(1, 5)],
        "simulation_helpers": ["part_01.rs"],
        "replay": ["part_01.rs"],
    }
    exports: dict[str, tuple[list[str], list[str]]] = {}
    section_paths: dict[str, list[Path]] = {}
    changed: set[Path] = set()

    for section, filenames in families.items():
        public: list[str] = []
        private: list[str] = []
        part_exports: list[tuple[str, list[str], list[str]]] = []
        paths: list[Path] = []
        for filename in filenames:
            path = crate / "lib_parts" / section / filename
            paths.append(path)
            part_public, part_private = transform_part(path, engine.rust_code_mask)
            public.extend(part_public)
            private.extend(part_private)
            part_exports.append((filename, part_public, part_private))
            changed.add(path)
        if len(public) != len(set(public)) or len(private) != len(set(private)):
            raise RuntimeError(f"duplicate section symbol: {section}")
        exports[section] = (public, private)
        section_paths[section] = paths

        if len(filenames) > 1:
            module_path = crate / "lib_parts" / section / "mod.rs"
            chunks = ["use super::*;\n\n"]
            for filename, part_public, part_private in part_exports:
                module = Path(filename).stem
                chunks.append(f"mod {module};\n")
                chunks.append(format_use("pub(crate) use", module, part_private))
                chunks.append(format_use("pub use", module, part_public))
                chunks.append("\n")
            module_path.write_text("".join(chunks), encoding="utf-8")
            changed.add(module_path)

    raw = {
        path: path.read_text(encoding="utf-8", errors="strict")
        for path in crate.rglob("*.rs")
    }
    masked = {path: engine.rust_code_mask(text) for path, text in raw.items()}
    for section, (public, private) in list(exports.items()):
        excluded = set(section_paths[section])
        excluded.add(crate / "lib_parts" / section / "mod.rs")
        outside_masked = [text for path, text in masked.items() if path not in excluded]
        outside_raw = [text for path, text in raw.items() if path not in excluded]
        exports[section] = (
            public,
            [
                name
                for name in private
                if word_used(name, outside_masked) or attribute_used(name, outside_raw)
            ],
        )

    lib = crate / "lib.rs"
    source = lib.read_text(encoding="utf-8", errors="strict")
    start = source.index(
        "// Ownership section: contracts_and_primitives. Ordinary Git-tracked source.\n"
    )
    end = source.index("// Ownership section: checkpoint_storage. Explicit ordinary Rust module.")
    blocks: list[str] = []
    for section in ("contracts_and_primitives", "mission_runtime", "simulation_helpers", "replay"):
        public, private = exports[section]
        block = (
            multipart_module(section, public, private)
            if len(families[section]) > 1
            else single_module(section, public, private)
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
            key = ("trillionnium/crates/trnm-rts-sim/src/lib.rs", expression)
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
    new_relative = "lib_parts/mission_runtime/mod.rs"
    if any(record["path"] == new_relative for record in manifest["parts"]):
        raise RuntimeError("mission_runtime/mod.rs already catalogued")
    manifest["parts"].append(
        {"bytes": 0, "path": new_relative, "section": "mission_runtime", "sha256": ""}
    )

    subprocess.run(
        ["cargo", "fmt", "--manifest-path", str(root / "trillionnium/Cargo.toml"), "--all"],
        check=True,
    )
    records = {record["path"]: record for record in manifest["parts"]}
    changed.add(manifest_path)
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

    print("WORLD_RTS_RUNTIME_MODULE_TRANSFORM=PASS seams=7")
    for section, (public, private) in exports.items():
        print(f"{section}: public={len(public)} cross_module_internal={len(private)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
