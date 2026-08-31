from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys

MAX_PART_BYTES = 60_000
MAX_IMPL_BYTES = 32_000
MAX_TEST_PART_BYTES = 28_000


def line_depths(text: str) -> list[int]:
    """Brace depth at the beginning of each source line, ignoring literals/comments."""
    depths: list[int] = []
    depth = 0
    index = 0
    length = len(text)
    state = "normal"
    block_depth = 0
    raw_hashes = 0
    at_line_start = True
    while index < length:
        if at_line_start:
            depths.append(depth)
            at_line_start = False
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""
        if state == "line_comment":
            if char == "\n":
                state = "normal"
                at_line_start = True
            index += 1
            continue
        if state == "block_comment":
            if char == "/" and next_char == "*":
                block_depth += 1
                index += 2
                continue
            if char == "*" and next_char == "/":
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "normal"
                continue
            if char == "\n":
                at_line_start = True
            index += 1
            continue
        if state == "string":
            if char == "\\":
                if next_char == "\n":
                    at_line_start = True
                index += 2
                continue
            if char == '"':
                state = "normal"
            if char == "\n":
                at_line_start = True
            index += 1
            continue
        if state == "char":
            if char == "\\":
                if next_char == "\n":
                    at_line_start = True
                index += 2
                continue
            if char == "'":
                state = "normal"
            if char == "\n":
                at_line_start = True
            index += 1
            continue
        if state == "raw":
            if char == '"' and text.startswith("#" * raw_hashes, index + 1):
                index += 1 + raw_hashes
                state = "normal"
            if char == "\n":
                at_line_start = True
            index += 1
            continue
        if char == "/" and next_char == "/":
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            state = "block_comment"
            block_depth = 1
            index += 2
            continue
        raw = False
        if char == "r":
            cursor = index + 1
            raw = True
        elif char == "b" and next_char == "r":
            cursor = index + 2
            raw = True
        else:
            cursor = index
        if raw:
            hashes = 0
            while cursor < length and text[cursor] == "#":
                hashes += 1
                cursor += 1
            if cursor < length and text[cursor] == '"':
                state = "raw"
                raw_hashes = hashes
                index = cursor + 1
                continue
        if char == '"' or (char == "b" and next_char == '"'):
            state = "string"
            index += 2 if char == "b" else 1
            continue
        if char == "'":
            cursor = index + 1
            if cursor < length and text[cursor] == "\\":
                cursor += 2
            else:
                cursor += 1
            if cursor < length and text[cursor] == "'":
                state = "char"
                index += 1
                continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise SystemExit(f"negative brace depth at byte {index}")
        if char == "\n":
            at_line_start = True
        index += 1
    if state == "block_comment":
        raise SystemExit("unterminated block comment")
    if depth != 0:
        raise SystemExit(f"unbalanced braces: {depth}")
    return depths[: len(text.splitlines())]


def member_starts(lines: list[str], depths: list[int], start: int, end: int) -> list[int]:
    starts: list[int] = []
    pending: int | None = None
    prefixes = (
        "pub fn ", "pub(crate) fn ", "pub(super) fn ", "fn ",
        "pub async fn ", "pub(crate) async fn ", "async fn ",
        "pub const ", "const ", "pub type ", "type ",
    )
    for index in range(start + 1, end):
        if depths[index] != 1:
            continue
        stripped = lines[index].lstrip()
        if not stripped:
            continue
        if stripped.startswith(("///", "#[", "//")):
            if pending is None:
                pending = index
            continue
        if stripped.startswith(prefixes):
            starts.append(pending if pending is not None else index)
            pending = None
        else:
            pending = None
    if not starts:
        raise SystemExit(f"no splittable members in {lines[start].strip()!r}")
    return sorted(set(starts))


def split_impl_blocks(text: str, signatures: list[str], max_bytes: int) -> str:
    for signature in signatures:
        while True:
            lines = text.splitlines(keepends=True)
            depths = line_depths(text)
            blocks: list[tuple[int, int]] = []
            cursor = 0
            while True:
                matches = [
                    index
                    for index in range(cursor, len(lines))
                    if depths[index] == 0 and lines[index].strip() == signature
                ]
                if not matches:
                    break
                start = matches[0]
                end = None
                for index in range(start + 1, len(lines)):
                    if depths[index] == 1 and lines[index].strip() == "}":
                        end = index
                        break
                if end is None:
                    raise SystemExit(f"unclosed block {signature!r}")
                blocks.append((start, end))
                cursor = end + 1
            oversized = [
                block
                for block in blocks
                if len("".join(lines[block[0] : block[1] + 1]).encode("utf-8")) > max_bytes
            ]
            if not oversized:
                break
            start, end = oversized[0]
            starts = member_starts(lines, depths, start, end)
            prefix = "".join(lines[start + 1 : starts[0]])
            items = []
            for item_index, item_start in enumerate(starts):
                item_end = starts[item_index + 1] if item_index + 1 < len(starts) else end
                items.append("".join(lines[item_start:item_end]))
            groups = []
            current = prefix
            for item in items:
                prospective = f"{signature}\n{current}{item}}}\n"
                if current.strip() and len(prospective.encode("utf-8")) > max_bytes:
                    groups.append(current)
                    current = item
                else:
                    current += item
            groups.append(current)
            if len(groups) <= 1:
                raise SystemExit(f"cannot split oversized block {signature!r}")
            replacement = "\n".join(f"{signature}\n{group}}}\n" for group in groups)
            text = "".join(lines[:start]) + replacement + "".join(lines[end + 1 :])
    return text


def semantic_item_starts(lines: list[str], depths: list[int], body_start: int = 0) -> list[int]:
    starts: list[int] = []
    pending: int | None = None
    prefixes = (
        "pub ", "fn ", "async fn ", "const ", "static ", "impl ",
        "mod ", "use ", "struct ", "enum ", "trait ", "type ",
        "macro_rules!", "unsafe ", "extern ",
    )
    for index in range(body_start, len(lines)):
        if depths[index] != 0:
            continue
        stripped = lines[index].lstrip()
        if not stripped:
            continue
        if stripped.startswith(("///", "#[", "//")):
            if pending is None:
                pending = index
            continue
        if stripped.startswith(prefixes):
            starts.append(pending if pending is not None else index)
            pending = None
        else:
            pending = None
    return sorted(set(starts))


def top_level_item_start(text: str, marker: str) -> int:
    lines = text.splitlines(keepends=True)
    depths = line_depths(text)
    starts = semantic_item_starts(lines, depths, 0)
    offsets = []
    total = 0
    for line in lines:
        offsets.append(total)
        total += len(line)
    candidates = []
    for index, line in enumerate(lines):
        if marker in line:
            candidates.append(offsets[index] + line.index(marker))
    if len(candidates) != 1:
        raise SystemExit(f"marker {marker!r}: expected one source occurrence, found {len(candidates)}")
    marker_offset = candidates[0]
    start_offsets = [offsets[index] for index in starts if offsets[index] <= marker_offset]
    if not start_offsets:
        raise SystemExit(f"marker {marker!r}: no containing top-level item")
    return start_offsets[-1]


def split_test_module(text: str, crate_prefix: str) -> tuple[str, dict[str, str]]:
    lines = text.splitlines(keepends=True)
    depths = line_depths(text)
    matches = [index for index, line in enumerate(lines) if depths[index] == 0 and line.strip() == "mod tests {"]
    if not matches:
        return text, {}
    if len(matches) != 1:
        raise SystemExit(f"{crate_prefix}: expected one top-level tests module, found {len(matches)}")
    start = matches[0]
    end = None
    for index in range(start + 1, len(lines)):
        if depths[index] == 1 and lines[index].strip() == "}":
            end = index
            break
    if end is None:
        raise SystemExit(f"{crate_prefix}: unclosed top-level tests module")
    starts = member_starts(lines, depths, start, end)
    prefix_text = "".join(lines[start + 1 : starts[0]])
    items = []
    for item_index, item_start in enumerate(starts):
        item_end = starts[item_index + 1] if item_index + 1 < len(starts) else end
        items.append("".join(lines[item_start:item_end]))
    chunks = []
    current = ""
    for item in items:
        candidate = current + item
        if current and len(candidate.encode("utf-8")) > MAX_TEST_PART_BYTES:
            chunks.append(current)
            current = item
        else:
            current = candidate
    if current:
        chunks.append(current)
    nested: dict[str, str] = {}
    includes = []
    for index, chunk in enumerate(chunks, 1):
        relative = f"tests/{crate_prefix}_tests_{index:02d}.rs"
        nested[relative] = chunk
        includes.append(
            "    include!(concat!(env!(\"CARGO_MANIFEST_DIR\"), "
            f"\"/src/lib_parts/{relative}\"));\n"
        )
    replacement = "mod tests {\n" + prefix_text + "".join(includes) + "}\n"
    return "".join(lines[:start]) + replacement + "".join(lines[end + 1 :]), nested


def chunk_section(section: str, label: str) -> list[str]:
    lines = section.splitlines(keepends=True)
    depths = line_depths(section)
    starts = semantic_item_starts(lines, depths, 0)
    if not starts:
        if section.strip():
            raise SystemExit(f"{label}: section has no top-level items")
        return []
    if starts[0] != 0:
        starts[0] = 0
    items = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        items.append("".join(lines[start:end]))
    chunks = []
    current = ""
    for item in items:
        size = len(item.encode("utf-8"))
        if size > MAX_PART_BYTES:
            raise SystemExit(f"{label}: unsplittable top-level item is {size} bytes")
        if current and len((current + item).encode("utf-8")) > MAX_PART_BYTES:
            chunks.append(current)
            current = item
        else:
            current += item
    if current:
        chunks.append(current)
    return chunks


def partition(
    path: Path,
    header_marker: str,
    sections: list[tuple[str, str]],
    impl_signatures: list[str],
    crate_prefix: str,
) -> None:
    original = path.read_text(encoding="utf-8")
    header_offset = top_level_item_start(original, header_marker)
    header = original[:header_offset].rstrip() + "\n\n"
    body = original[header_offset:]
    body = split_impl_blocks(body, impl_signatures, MAX_IMPL_BYTES)
    body, nested_tests = split_test_module(body, crate_prefix)

    positions = [(label, top_level_item_start(body, marker)) for label, marker in sections]
    if [position for _label, position in positions] != sorted(position for _label, position in positions):
        raise SystemExit(f"{path}: semantic section markers are not ordered")
    if len({position for _label, position in positions}) != len(positions):
        raise SystemExit(f"{path}: semantic section markers overlap")

    parts_dir = path.parent / "lib_parts"
    if parts_dir.exists():
        for candidate in sorted(parts_dir.rglob("*"), reverse=True):
            if candidate.is_file() or candidate.is_symlink():
                candidate.unlink()
            elif candidate.is_dir():
                candidate.rmdir()
    parts_dir.mkdir(parents=True, exist_ok=True)

    records = []
    includes = []
    reconstructed = ""
    for section_index, (label, start) in enumerate(positions):
        end = positions[section_index + 1][1] if section_index + 1 < len(positions) else len(body)
        chunks = chunk_section(body[start:end], label)
        if not chunks:
            raise SystemExit(f"{path}: empty semantic section {label}")
        for part_index, chunk in enumerate(chunks, 1):
            relative = f"{label}/part_{part_index:02d}.rs"
            destination = parts_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(chunk, encoding="utf-8")
            raw = chunk.encode("utf-8")
            records.append({
                "section": label,
                "path": f"lib_parts/{relative}",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
            includes.append(
                f"// Ownership section: {label}. Ordinary Git-tracked source.\n"
                f"include!(\"lib_parts/{relative}\");"
            )
            reconstructed += chunk
    if reconstructed != body:
        raise SystemExit(f"{path}: semantic partition did not preserve the transformed body")

    nested_records = []
    for relative, content in sorted(nested_tests.items()):
        destination = parts_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        raw = content.encode("utf-8")
        nested_records.append({
            "section": "tests",
            "path": f"lib_parts/{relative}",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })

    path.write_text(
        header
        + "// Correctness-critical implementation is partitioned by ownership. Every\n"
          "// included file is ordinary reviewed source; no build script rewrites runtime\n"
          "// semantics and no generated Rust source participates in compilation.\n\n"
        + "\n\n".join(includes)
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": "trnm_semantic_direct_source_partition_v1",
        "crate": path.parent.parent.name,
        "semantic_generation": False,
        "textual_include_reason": "preserve the reviewed crate-root API while shrinking ownership and review radius",
        "max_part_bytes": MAX_PART_BYTES,
        "max_impl_bytes": MAX_IMPL_BYTES,
        "sections": [label for label, _marker in sections],
        "parts": records,
        "nested_test_parts": nested_records,
    }
    (parts_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    largest = max(record["bytes"] for record in records + nested_records)
    if largest > MAX_PART_BYTES:
        raise SystemExit(f"{path}: partition contains oversized {largest}-byte file")
    print(json.dumps({
        "path": str(path),
        "sections": len(sections),
        "parts": len(records),
        "nested_tests": len(nested_records),
        "max_bytes": largest,
    }, sort_keys=True))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: partition-semantic-rust.py <world-root> <game-server|campaign|rts>")
    root = Path(sys.argv[1])
    mode = sys.argv[2]
    if mode == "game-server":
        partition(
            root / "trillionnium/crates/trnm-game-server/src/lib.rs",
            "use axum::extract::DefaultBodyLimit;",
            [
                ("authority_foundation", "use axum::extract::DefaultBodyLimit;"),
                ("configuration_and_migrations", "pub struct AppStateConfig"),
                ("terminal_recovery", "struct ApiError"),
                ("operations_boundary", "pub fn validate_operations_bind_addr"),
                ("fleet_fencing", "async fn lock_current_fleet_epoch("),
                ("identity", "fn session_header"),
                ("application", "fn mission_for_map"),
                ("http_routing", "pub fn build_router"),
                ("readiness", "async fn health"),
                ("product_api", "async fn connect_campaign"),
                ("actor_runtime", "pub fn production_authority_tick_interval"),
                ("campaign_persistence", "async fn persist_campaign("),
                ("tests", "#[cfg(test)]"),
            ],
            [],
            "game_server",
        )
    elif mode == "campaign":
        partition(
            root / "trillionnium/crates/trnm-campaign-core/src/lib.rs",
            "pub const CAMPAIGN_SAVE_CONTRACT",
            [
                ("contracts_and_domain", "pub const CAMPAIGN_SAVE_CONTRACT"),
                ("authored_content", "pub enum StoryQuestId"),
                ("campaign_state", "pub struct CampaignSaveV1"),
                ("campaign_commands", "/// Executes one public campaign command"),
                ("rts_mapping", "pub fn typed_equipment_modifier"),
                ("save_slots", "pub enum SaveSlotId"),
                ("player_settings", "pub const PLAYER_SETTINGS_CONTRACT"),
                ("campaign_storage", "pub struct CampaignStore"),
                ("economy_commands", "fn effective_economy_binding"),
                ("tests", "#[cfg(test)]"),
            ],
            ["impl CampaignSaveV1 {"],
            "campaign",
        )
    elif mode == "rts":
        partition(
            root / "trillionnium/crates/trnm-rts-sim/src/lib.rs",
            "pub const RTS_SIM_CONTRACT",
            [
                ("contracts_and_primitives", "pub const RTS_SIM_CONTRACT"),
                ("mission_runtime", "pub struct MissionSimV1"),
                ("simulation_helpers", "fn is_aftershock_map"),
                ("replay", "impl BattleReplayV1"),
                ("checkpoint_storage", "pub struct SimCheckpointV1"),
                ("tests", "#[cfg(test)]"),
            ],
            ["impl MissionSimV1 {"],
            "rts",
        )
    else:
        raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    main()
