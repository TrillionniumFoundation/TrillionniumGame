#!/usr/bin/env python3
"""Extract and merge the pinned TypeScript Runtime interface surface.

The original D4 parser intentionally accepts only a small declaration grammar.
This independent pass walks balanced TypeScript tokens and extracts every
interface and top-level interface member even when preceding declarations use
`const enum` or object type aliases without trailing semicolons.

The pass never grants SG1 or compatibility credit. It only prevents real
pinned interface declarations from disappearing from the candidate leaf set.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = ROOT / "scripts/generate-runtime-denominator.py"
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "runtime_denominator_generator_for_typescript_pass", GENERATOR_PATH
)
if GENERATOR_SPEC is None or GENERATOR_SPEC.loader is None:
    raise RuntimeError("cannot load Runtime denominator generator")
GENERATOR = importlib.util.module_from_spec(GENERATOR_SPEC)
sys.modules[GENERATOR_SPEC.name] = GENERATOR
GENERATOR_SPEC.loader.exec_module(GENERATOR)

EXTRACTOR = "trillionnium-runtime-typescript-interface-pass"
VERSION = "0.1.0"
MODIFIERS = {
    "export",
    "declare",
    "default",
    "abstract",
    "readonly",
    "public",
    "private",
    "protected",
    "static",
}


class TypeScriptSurfaceError(RuntimeError):
    """Raised when the pinned TypeScript interface surface cannot be extracted."""


@dataclass(frozen=True, slots=True)
class Token:
    value: str
    kind: str
    line: int


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def lex(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    while index < len(text):
        char = text[index]
        if char in " \t\r":
            index += 1
        elif char == "\n":
            line += 1
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise TypeScriptSurfaceError(
                    f"unterminated TypeScript comment at line {line}"
                )
            line += text.count("\n", index, end + 2)
            index = end + 2
        elif char in {'"', "'", "`"}:
            quote = char
            start = index
            start_line = line
            index += 1
            escaped = False
            template_depth = 0
            while index < len(text):
                current = text[index]
                if current == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif quote == "`" and text.startswith("${", index):
                    template_depth += 1
                    index += 1
                elif quote == "`" and current == "}" and template_depth:
                    template_depth -= 1
                elif current == quote and template_depth == 0:
                    index += 1
                    break
                index += 1
            else:
                raise TypeScriptSurfaceError(
                    f"unterminated TypeScript string at line {start_line}"
                )
            tokens.append(Token(text[start:index], "string", start_line))
        elif char.isalpha() or char in "_$":
            start = index
            index += 1
            while index < len(text) and (
                text[index].isalnum() or text[index] in "_$"
            ):
                index += 1
            tokens.append(Token(text[start:index], "ident", line))
        elif char.isdigit():
            start = index
            index += 1
            while index < len(text) and (
                text[index].isalnum() or text[index] in ".xX_"
            ):
                index += 1
            tokens.append(Token(text[start:index], "number", line))
        else:
            three = text[index : index + 3]
            two = text[index : index + 2]
            if three in {"...", ">>>", "===", "!=="}:
                tokens.append(Token(three, "symbol", line))
                index += 3
            elif two in {
                "=>",
                "<=",
                ">=",
                "==",
                "!=",
                "&&",
                "||",
                "??",
                "?.",
                "::",
                "++",
                "--",
                "<<",
                ">>",
            }:
                tokens.append(Token(two, "symbol", line))
                index += 2
            else:
                tokens.append(Token(char, "symbol", line))
                index += 1
    return tokens


def normalize(tokens: Iterable[Token]) -> str:
    values = [token.value for token in tokens]
    result = ""
    no_space_before = {",", ";", ":", ")", "]", "}", ">", ".", "?", "!"}
    for value in values:
        if (
            result
            and value not in no_space_before
            and result[-1] not in "([{<."
            and not any(result.endswith(marker) for marker in ("=>", "?."))
        ):
            result += " "
        result += value
    return " ".join(result.split())


def qualify(namespace: str, name: str) -> str:
    clean = name.strip("\"'`")
    return f"{namespace}.{clean}" if namespace else clean


def member_name(member: Sequence[Token]) -> str | None:
    filtered = [
        token
        for token in member
        if token.value
        not in {
            "readonly",
            "public",
            "private",
            "protected",
            "static",
            "abstract",
            "declare",
            "?",
            ";",
            ",",
        }
    ]
    if not filtered:
        return None
    if filtered[0].value == "(":
        return "[call]"
    if filtered[0].value == "[":
        return "[index]"
    if filtered[0].value in {"new", "constructor"}:
        return filtered[0].value
    if filtered[0].value in {"get", "set"} and len(filtered) > 1:
        return filtered[1].value.strip("\"'`")
    for token in filtered:
        if token.kind in {"ident", "string"}:
            return token.value.strip("\"'`")
    return None


def _append_member(
    items: list[dict[str, Any]],
    manual: list[dict[str, Any]],
    *,
    path: str,
    container: str,
    tokens: Sequence[Token],
) -> None:
    member = list(tokens)
    while member and member[0].value in {";", ","}:
        member.pop(0)
    while member and member[-1].value in {";", ","}:
        member.pop()
    if not member:
        return
    signature = normalize(member)
    name = member_name(member)
    if name is None:
        manual.append(
            {
                "class": "typescript_unparsed_interface_member",
                "symbol": container,
                "path": path,
                "start_line": member[0].line,
                "end_line": member[-1].line,
                "signature": signature,
            }
        )
        return
    items.append(
        {
            "class": "typescript_interface_member",
            "symbol": f"{container}.{name}",
            "signature": signature,
            "path": path,
            "start_line": member[0].line,
            "end_line": member[-1].line,
            "metadata": {"extractor": EXTRACTOR, "version": VERSION},
        }
    )


def extract_interfaces(
    text: str, path: str = "index.d.ts"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tokens = lex(text)
    items: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    namespaces: list[tuple[str, int]] = []
    brace_depth = 0
    index = 0

    while index < len(tokens):
        token = tokens[index]

        if token.value in {"namespace", "module"}:
            if index + 1 >= len(tokens) or tokens[index + 1].kind not in {
                "ident",
                "string",
            }:
                index += 1
                continue
            name = tokens[index + 1].value
            opening = index + 2
            while opening < len(tokens) and tokens[opening].value != "{":
                opening += 1
            if opening >= len(tokens):
                manual.append(
                    {
                        "class": "typescript_unparsed_namespace",
                        "symbol": name,
                        "path": path,
                        "start_line": token.line,
                        "end_line": tokens[-1].line,
                        "signature": normalize(tokens[index:]),
                    }
                )
                break
            parent = namespaces[-1][0] if namespaces else ""
            symbol = qualify(parent, name)
            brace_depth += 1
            namespaces.append((symbol, brace_depth))
            index = opening + 1
            continue

        if token.value == "interface":
            if index + 1 >= len(tokens) or tokens[index + 1].kind != "ident":
                manual.append(
                    {
                        "class": "typescript_unparsed_interface",
                        "symbol": namespaces[-1][0] if namespaces else "<root>",
                        "path": path,
                        "start_line": token.line,
                        "end_line": token.line,
                        "signature": token.value,
                    }
                )
                index += 1
                continue
            name = tokens[index + 1]
            opening = index + 2
            angle = 0
            while opening < len(tokens):
                value = tokens[opening].value
                if value == "<":
                    angle += 1
                elif value == ">":
                    angle = max(0, angle - 1)
                elif value == "{" and angle == 0:
                    break
                opening += 1
            if opening >= len(tokens):
                manual.append(
                    {
                        "class": "typescript_unparsed_interface",
                        "symbol": name.value,
                        "path": path,
                        "start_line": token.line,
                        "end_line": tokens[-1].line,
                        "signature": normalize(tokens[index:]),
                    }
                )
                break

            header_start = index
            while (
                header_start > 0
                and tokens[header_start - 1].value in MODIFIERS
                and tokens[header_start - 1].line == token.line
            ):
                header_start -= 1
            namespace = namespaces[-1][0] if namespaces else ""
            symbol = qualify(namespace, name.value)
            header = tokens[header_start:opening]
            items.append(
                {
                    "class": "typescript_interface",
                    "symbol": symbol,
                    "signature": normalize(header),
                    "path": path,
                    "start_line": header[0].line,
                    "end_line": header[-1].line,
                    "metadata": {"extractor": EXTRACTOR, "version": VERSION},
                }
            )

            member_start = opening + 1
            cursor = member_start
            paren = bracket = nested_brace = angle = 0
            while cursor < len(tokens):
                current = tokens[cursor]
                value = current.value
                if (
                    value == "}"
                    and paren == bracket == nested_brace == angle == 0
                ):
                    _append_member(
                        items,
                        manual,
                        path=path,
                        container=symbol,
                        tokens=tokens[member_start:cursor],
                    )
                    break
                if (
                    value in {";", ","}
                    and paren == bracket == nested_brace == angle == 0
                ):
                    _append_member(
                        items,
                        manual,
                        path=path,
                        container=symbol,
                        tokens=tokens[member_start : cursor + 1],
                    )
                    member_start = cursor + 1
                elif value == "(":
                    paren += 1
                elif value == ")":
                    paren = max(0, paren - 1)
                elif value == "[":
                    bracket += 1
                elif value == "]":
                    bracket = max(0, bracket - 1)
                elif value == "{":
                    nested_brace += 1
                elif value == "}":
                    nested_brace = max(0, nested_brace - 1)
                elif value == "<":
                    angle += 1
                elif value == ">":
                    angle = max(0, angle - 1)
                cursor += 1
            else:
                manual.append(
                    {
                        "class": "typescript_unterminated_interface",
                        "symbol": symbol,
                        "path": path,
                        "start_line": token.line,
                        "end_line": tokens[-1].line,
                        "signature": normalize(tokens[index:]),
                    }
                )
                break
            index = cursor + 1
            continue

        if token.value == "{":
            brace_depth += 1
        elif token.value == "}":
            if namespaces and namespaces[-1][1] == brace_depth:
                namespaces.pop()
            brace_depth = max(0, brace_depth - 1)
        index += 1

    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = (str(item["class"]), str(item["symbol"]), str(item["signature"]))
        unique[key] = item
    return (
        sorted(
            unique.values(),
            key=lambda item: (
                str(item["class"]),
                str(item["symbol"]),
                str(item["signature"]),
            ),
        ),
        sorted(manual, key=canonical),
    )


def augment(common_root: Path, output_root: Path) -> dict[str, Any]:
    source_path = common_root / "index.d.ts"
    manifest_path = output_root / "runtime-denominator.candidate.json"
    if not source_path.is_file():
        raise TypeScriptSurfaceError(f"missing pinned declaration file: {source_path}")
    if not manifest_path.is_file():
        raise TypeScriptSurfaceError(f"missing Runtime candidate manifest: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "trillionnium.runtime-denominator-candidate.v1":
        raise TypeScriptSurfaceError("Runtime candidate schema mismatch")
    items, manual = extract_interfaces(
        source_path.read_text(encoding="utf-8"), "index.d.ts"
    )
    interface_count = sum(item["class"] == "typescript_interface" for item in items)
    member_count = sum(
        item["class"] == "typescript_interface_member" for item in items
    )
    if interface_count == 0 or member_count == 0:
        raise TypeScriptSurfaceError(
            "pinned TypeScript source produced an empty interface surface"
        )

    retained = [
        leaf
        for leaf in manifest.get("leaves", [])
        if leaf.get("class")
        not in {"typescript_interface", "typescript_interface_member"}
    ]
    extracted = [
        GENERATOR.make_leaf(
            item,
            GENERATOR.COMMON_REPOSITORY,
            GENERATOR.COMMON_COMMIT,
            common_root,
        )
        for item in items
    ]
    leaves = sorted([*retained, *extracted], key=lambda leaf: leaf["id"])
    if len({leaf["id"] for leaf in leaves}) != len(leaves):
        raise TypeScriptSurfaceError("duplicate stable Runtime leaf IDs after augmentation")

    manual_contracts = list(manifest.get("manual_contracts", []))
    manual_contracts.extend(
        {"source": "typescript-interface-pass", **entry} for entry in manual
    )
    manual_contracts.sort(key=GENERATOR.canonical)

    counts: dict[str, int] = {}
    for leaf in leaves:
        class_name = str(leaf["class"])
        counts[class_name] = counts.get(class_name, 0) + 1

    manifest.pop("content_sha256", None)
    manifest["leaves"] = leaves
    manifest["leaf_count"] = len(leaves)
    manifest["unclassified_count"] = len(leaves)
    manifest["unreviewed_count"] = len(leaves)
    manifest["manual_contracts"] = manual_contracts
    manifest["manual_contract_count"] = len(manual_contracts)
    manifest["counts_by_class"] = counts
    manifest["typescript_interface_pass"] = {
        "name": EXTRACTOR,
        "version": VERSION,
        "interface_count": interface_count,
        "member_count": member_count,
        "manual_contract_count": len(manual),
    }
    manifest["content_sha256"] = GENERATOR.sha256(GENERATOR.canonical(manifest))

    reconciliation = GENERATOR.reconciliation(leaves)
    reconciliation["runtime_manifest_sha256"] = manifest["content_sha256"]
    GENERATOR.write_json(manifest_path, manifest)
    GENERATOR.write_json(
        output_root / "runtime-language-reconciliation.candidate.json",
        reconciliation,
    )
    checksum_rows = []
    for path in sorted(output_root.glob("*.json")):
        checksum_rows.append(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        )
    (output_root / "SHA256SUMS").write_text(
        "\n".join(checksum_rows) + "\n", encoding="utf-8"
    )
    return {
        "interface_count": interface_count,
        "member_count": member_count,
        "manual_contract_count": len(manual),
        "leaf_count": len(leaves),
        "compatibility_credit": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--common-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = augment(args.common_dir.resolve(), args.output_dir.resolve())
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeScriptSurfaceError,
    ) as error:
        print(f"Runtime TypeScript interface augmentation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
