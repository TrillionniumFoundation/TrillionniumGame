#!/usr/bin/env python3
"""Convert every remaining campaign-core runtime include seam into Rust modules.

The transform preserves the original crate-root public API, widens only
inherent-implementation members and private fields to crate visibility, imports
only private top-level symbols that are actually referenced outside their
owning module, records every removed seam, and refreshes the closed-world source
manifest after rustfmt has produced the final bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

IDENT = r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*"
VIS_RE = r"(?P<vis>pub(?:\([^)]*\))?\s+)?"
FN_RE = re.compile(
    rf"^(?P<indent>\s*){VIS_RE}(?P<prefix>(?:(?:unsafe|async|const)\s+|extern\s+\"[^\"]+\"\s+)*)fn\s+(?P<name>{IDENT})"
)
OTHER_RE = re.compile(
    rf"^(?P<indent>\s*){VIS_RE}(?P<kind>struct|enum|union|trait|type|const|static)\s+(?P<name>{IDENT})"
)
IMPL_RE = re.compile(r"^\s*(?:unsafe\s+)?impl\b")
FIELD_RE = re.compile(
    rf"^(?P<indent>\s*)(?P<vis>pub(?:\([^)]*\))?\s+)?(?P<name>{IDENT})\s*:"
)
ASSOC_OTHER_RE = re.compile(
    rf"^(?P<indent>\s*){VIS_RE}(?P<kind>type|const)\s+(?P<name>{IDENT})"
)


def external_public(visibility: str | None) -> bool:
    return visibility == "pub "


def add_visibility(
    line: str,
    match: re.Match[str],
    visibility: str = "pub(crate) ",
) -> str:
    if match.groupdict().get("vis"):
        return line
    start = len(match.group("indent"))
    return line[:start] + visibility + line[start:]


def top_item(line: str) -> re.Match[str] | None:
    return FN_RE.match(line) or OTHER_RE.match(line)


def classify_impl(header: str) -> str:
    """Distinguish inherent impls from trait impls before member rewriting."""
    before_open = header.split("{", 1)[0]
    return "trait_impl" if re.search(r"\bfor\b", before_open) else "impl"


def transform_part(path: Path, mask_fn) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8", errors="strict")
    if text.startswith("use super::*;\n"):
        raise RuntimeError(f"already transformed: {path}")
    mask = mask_fn(text)
    lines = text.splitlines(keepends=True)
    masked = mask.splitlines(keepends=True)
    if len(lines) != len(masked):
        raise RuntimeError(f"line-count drift after Rust masking: {path}")

    depth = 0
    pending_outer: str | None = None
    pending_impl_header = ""
    outer_kind: str | None = None
    public: list[str] = []
    internal: list[str] = []
    output: list[str] = []

    for line, masked_line in zip(lines, masked):
        if depth == 0:
            if pending_outer == "impl_pending":
                pending_impl_header += masked_line
            elif IMPL_RE.match(masked_line):
                pending_outer = "impl_pending"
                pending_impl_header = masked_line
            else:
                match = top_item(masked_line)
                if match:
                    kind = "fn" if FN_RE.match(masked_line) else match.group("kind")
                    name = match.group("name")
                    if external_public(match.groupdict().get("vis")):
                        public.append(name)
                    else:
                        internal.append(name)
                        line = add_visibility(line, match)
                    pending_outer = kind
        elif depth == 1 and outer_kind == "struct":
            field = FIELD_RE.match(masked_line)
            if field and not field.groupdict().get("vis"):
                line = add_visibility(line, field)
        elif depth == 1 and outer_kind == "impl":
            # Inherent methods may need sibling-module access. Trait items may
            # never carry an explicit visibility qualifier (Rust E0449).
            associated = FN_RE.match(masked_line) or ASSOC_OTHER_RE.match(masked_line)
            if associated and not associated.groupdict().get("vis"):
                line = add_visibility(line, associated)

        output.append(line)
        opens = masked_line.count("{")
        closes = masked_line.count("}")
        before = depth
        depth += opens - closes
        if before == 0 and opens:
            if pending_outer == "impl_pending":
                outer_kind = classify_impl(pending_impl_header)
            else:
                outer_kind = pending_outer
            pending_outer = None
            pending_impl_header = ""
        if depth == 0:
            outer_kind = None
            if ";" in masked_line and opens == 0:
                pending_outer = None
                pending_impl_header = ""

    if depth != 0:
        raise RuntimeError(f"unbalanced braces after masking: {path}: {depth}")
    if len(public) != len(set(public)) or len(internal) != len(set(internal)):
        raise RuntimeError(f"duplicate top-level declaration in {path}")

    path.write_text("use super::*;\n\n" + "".join(output), encoding="utf-8")
    return public, internal


def format_use(prefix: str, module: str, names: list[str]) -> str:
    if not names:
        return ""
    values = sorted(names)
    if len(values) <= 3 and sum(map(len, values)) < 70:
        return f"{prefix} {module}::{{{', '.join(values)}}};\n"
    body = "\n".join(f"    {value}," for value in values)
    return f"{prefix} {module}::{{\n{body}\n}};\n"


def single_module(section: str, public: list[str], internal: list[str]) -> str:
    return (
        f"// Ownership module: {section}. Ordinary Git-tracked Rust module.\n"
        f"#[path = \"lib_parts/{section}/part_01.rs\"]\n"
        f"mod {section};\n"
        + format_use("use", section, internal)
        + format_use("pub use", section, public)
    )


def multipart_module(section: str, public: list[str], internal: list[str]) -> str:
    return (
        f"// Ownership module: {section}. Multi-part implementation is isolated\n"
        f"// behind one explicit module with crate-private cross-part visibility.\n"
        f"#[path = \"lib_parts/{section}/mod.rs\"]\n"
        f"mod {section};\n"
        + format_use("use", section, internal)
        + format_use("pub use", section, public)
    )


def word_used(name: str, texts: list[str]) -> bool:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
    )
    return any(pattern.search(text) for text in texts)


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

    crate = root / "trillionnium/crates/trnm-campaign-core/src"
    families = {
        "contracts_and_domain": ["part_01.rs"],
        "authored_content": ["part_01.rs"],
        "campaign_state": ["part_01.rs"],
        "campaign_commands": [f"part_{index:02d}.rs" for index in range(1, 7)],
        "economy_commands": ["part_01.rs"],
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
            public, internal = transform_part(path, engine.rust_code_mask)
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
                chunks.append(format_use("pub(crate) use", module, internal))
                chunks.append(format_use("pub use", module, public))
                chunks.append("\n")
            module_path.write_text("".join(chunks), encoding="utf-8")
            changed.add(module_path)

    # Keep a private symbol at the crate root only when another module or the
    # test surface actually references it. This avoids masking bad extraction
    # with unused-import allowances.
    masked_by_path = {
        path: engine.rust_code_mask(path.read_text(encoding="utf-8", errors="strict"))
        for path in crate.rglob("*.rs")
    }
    for section, (public, internal) in list(exports.items()):
        excluded = set(section_paths[section])
        excluded.add(crate / "lib_parts" / section / "mod.rs")
        outside = [
            text for path, text in masked_by_path.items()
            if path not in excluded
        ]
        exports[section] = (
            public,
            [name for name in internal if word_used(name, outside)],
        )

    lib = crate / "lib.rs"
    source = lib.read_text(encoding="utf-8", errors="strict")
    start_marker = "// Ownership section: contracts_and_domain. Ordinary Git-tracked source.\n"
    end_marker = "// RTS mapping is an explicit module."
    start = source.index(start_marker)
    end = source.index(end_marker)
    blocks: list[str] = []
    for section in ("contracts_and_domain", "authored_content", "campaign_state"):
        public, internal = exports[section]
        blocks.extend((single_module(section, public, internal), "\n"))
    public, internal = exports["campaign_commands"]
    blocks.extend((multipart_module("campaign_commands", public, internal), "\n"))
    source = source[:start] + "".join(blocks) + source[end:]

    old_economy = (
        "// Ownership section: economy_commands. Ordinary Git-tracked source.\n"
        "include!(\"lib_parts/economy_commands/part_01.rs\");\n"
    )
    public, internal = exports["economy_commands"]
    if source.count(old_economy) != 1:
        raise RuntimeError("economy include anchor drift")
    source = source.replace(
        old_economy,
        single_module("economy_commands", public, internal),
    )
    lib.write_text(source, encoding="utf-8")
    changed.add(lib)

    ledger_path = root / "scripts/contracts/trnm-world-include-migrations-v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    existing = {(item["path"], item["expression"]) for item in ledger["completed"]}
    for section, filenames in families.items():
        replacement_path = "mod.rs" if len(filenames) > 1 else filenames[0]
        for filename in filenames:
            expression = f'"lib_parts/{section}/{filename}"'
            key = (
                "trillionnium/crates/trnm-campaign-core/src/lib.rs",
                expression,
            )
            if key in existing:
                raise RuntimeError(f"already completed: {key}")
            ledger["completed"].append(
                {
                    "path": key[0],
                    "expression": expression,
                    "replacement_module": section,
                    "required_fragments": [
                        f'#[path = "lib_parts/{section}/{replacement_path}"]',
                        f"mod {section};",
                    ],
                    "completed_commit": args.completed_commit,
                }
            )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    manifest_path = crate / "lib_parts/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    new_relative = "lib_parts/campaign_commands/mod.rs"
    if any(record["path"] == new_relative for record in manifest["parts"]):
        raise RuntimeError("campaign_commands/mod.rs is already catalogued")
    manifest["parts"].append(
        {
            "bytes": 0,
            "path": new_relative,
            "section": "campaign_commands",
            "sha256": "",
        }
    )

    # Rustfmt first; the closed-world manifest must bind the final bytes.
    subprocess.run(
        [
            "cargo",
            "fmt",
            "--manifest-path",
            str(root / "trillionnium/Cargo.toml"),
            "--all",
        ],
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
        key=lambda record: (
            manifest["sections"].index(record["section"]),
            record["path"],
        ),
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print("WORLD_CAMPAIGN_RUNTIME_MODULE_TRANSFORM=PASS seams=10")
    for section, (public, internal) in exports.items():
        print(f"{section}: public={len(public)} cross_module_internal={len(internal)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
