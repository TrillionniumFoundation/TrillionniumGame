#!/usr/bin/env python3
"""Generate a fail-closed Nakama Runtime parity-denominator candidate.

Inputs must be complete source trees produced by the reviewed pinned-source
fetcher. The output is deterministic and intentionally grants no SG1, C2,
production, or replacement credit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.upstream.pinned_archive import SourceArchiveError, git_blob_sha1_bytes, verify_source_lock  # noqa: E402

GENERATOR = "trillionniumgame-runtime-denominator"
VERSION = "0.1.0"
NAKAMA_REPOSITORY = "heroiclabs/nakama"
NAKAMA_COMMIT = "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09"
NAKAMA_TREE = "f3c9cfc2726d5543da1564629170f35b98e3797d"
COMMON_REPOSITORY = "heroiclabs/nakama-common"
COMMON_COMMIT = "449b77ecc8789aa466c36b67f6e498033dfcd9c5"
COMMON_TREE = "c6a7b9796b9c2a6b5118c74e5f213963a5001f14"


class DenominatorError(RuntimeError):
    pass


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


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def stable_id(item_class: str, path: str, symbol: str, signature: str) -> str:
    seed = f"D4\0{item_class}\0{path}\0{symbol}\0{signature}".encode("utf-8")
    return "TG-D4-" + hashlib.sha256(seed).hexdigest()[:20].upper()


def source_descriptor(repository: str, commit: str, root: Path, path: str, start: int | None, end: int | None) -> dict[str, Any]:
    data = (root / path).read_bytes()
    return {
        "repository": repository,
        "commit": commit,
        "path": path,
        "blob": git_blob_sha1_bytes(data),
        "sha256": sha256(data),
        "start_line": start,
        "end_line": end,
    }


def make_leaf(item: dict[str, Any], repository: str, commit: str, root: Path) -> dict[str, Any]:
    signature = str(item.get("signature", ""))
    symbol = str(item["symbol"])
    item_class = str(item["class"])
    path = str(item["path"])
    identifier = stable_id(item_class, path, symbol, signature)
    contract = {
        "class": item_class,
        "symbol": symbol,
        "signature": signature,
        "metadata": item.get("metadata") or {},
    }
    return {
        "id": identifier,
        "layer": "D4",
        "class": item_class,
        "symbol": symbol,
        "signature_hash": sha256(canonical(contract)),
        "source": source_descriptor(
            repository,
            commit,
            root,
            path,
            item.get("start_line"),
            item.get("end_line"),
        ),
        "compatibility_profile": "C2",
        "stability_tier": "runtime-behavior-contract",
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": "runtime-vm",
        "workstream": "W11",
        "task_ids": ["TG-W0-002"],
        "test_ids": [f"TG-DIFF-{identifier}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def run_go_extractor(root: Path, paths: Sequence[str], include_unexported: bool = False) -> dict[str, Any]:
    command = [
        "go",
        "run",
        str(ROOT / "tools/denominator/go_runtime_surface.go"),
        "--root",
        str(root),
    ]
    if include_unexported:
        command.append("--include-unexported")
    command.extend(paths)
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        raise DenominatorError(f"Go surface extraction failed: {completed.stderr.strip()}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DenominatorError(f"Go extractor emitted invalid JSON: {exc}") from exc
    if value.get("schema") != "trillionnium.go-runtime-surface.v1":
        raise DenominatorError("Go extractor schema mismatch")
    return value


def lex_typescript(text: str) -> list[Token]:
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
                raise DenominatorError(f"unterminated TypeScript comment at line {line}")
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
                raise DenominatorError(f"unterminated TypeScript string at line {start_line}")
            tokens.append(Token(text[start:index], "string", start_line))
        elif char.isalpha() or char in "_$":
            start = index
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] in "_$"):
                index += 1
            tokens.append(Token(text[start:index], "ident", line))
        elif char.isdigit():
            start = index
            index += 1
            while index < len(text) and (text[index].isalnum() or text[index] in ".xX_"):
                index += 1
            tokens.append(Token(text[start:index], "number", line))
        else:
            three = text[index:index + 3]
            two = text[index:index + 2]
            if three in {"...", ">>>", "===", "!=="}:
                tokens.append(Token(three, "symbol", line))
                index += 3
            elif two in {"=>", "<=", ">=", "==", "!=", "&&", "||", "??", "?.", "::", "++", "--", "<<", ">>"}:
                tokens.append(Token(two, "symbol", line))
                index += 2
            else:
                tokens.append(Token(char, "symbol", line))
                index += 1
    return tokens


def normalize_tokens(tokens: Iterable[Token]) -> str:
    values = [token.value for token in tokens]
    result = ""
    no_space_before = {",", ";", ":", ")", "]", "}", ">", ".", "?", "!"}
    for value in values:
        if result and value not in no_space_before and result[-1] not in "([{<." and not any(result.endswith(marker) for marker in ("=>", "?.")):
            result += " "
        result += value
    return " ".join(result.split())


class TypeScriptParser:
    MODIFIERS = {"export", "declare", "default", "abstract", "readonly"}

    def __init__(self, text: str, path: str):
        self.tokens = lex_typescript(text)
        self.path = path
        self.index = 0
        self.items: list[dict[str, Any]] = []
        self.manual: list[dict[str, Any]] = []

    def peek(self, offset: int = 0) -> Token | None:
        position = self.index + offset
        return self.tokens[position] if position < len(self.tokens) else None

    def pop(self) -> Token:
        token = self.peek()
        if token is None:
            raise DenominatorError("unexpected end of TypeScript declaration")
        self.index += 1
        return token

    def accept(self, value: str) -> Token | None:
        token = self.peek()
        if token and token.value == value:
            self.index += 1
            return token
        return None

    def parse(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            self.parse_scope("")
        except DenominatorError as exc:
            # The pinned declaration file contains constructs outside this
            # deliberately small parser. Preserve all successfully extracted
            # leaves and convert the unresolved suffix into an explicit manual
            # contract instead of dropping it or aborting the whole D4 lane.
            start = min(self.index, max(0, len(self.tokens) - 1))
            residual = self.tokens[start:]
            self.manual.append(
                {
                    "class": "typescript_parser_residual_manual_contract",
                    "symbol": "<parser-residual>",
                    "path": self.path,
                    "start_line": residual[0].line if residual else None,
                    "end_line": residual[-1].line if residual else None,
                    "signature": normalize_tokens(residual),
                    "reason": str(exc),
                    "token_count": len(residual),
                }
            )
            self.index = len(self.tokens)
        return self.items, self.manual

    def parse_scope(self, prefix: str, closing: str | None = None) -> None:
        while self.peek():
            if closing and self.peek().value == closing:
                self.pop()
                return
            modifiers: list[Token] = []
            while self.peek() and self.peek().value in self.MODIFIERS:
                modifiers.append(self.pop())
            token = self.peek()
            if token is None:
                return
            if token.value in {"namespace", "module"}:
                self.parse_namespace(prefix, modifiers)
            elif token.value in {"interface", "class"}:
                self.parse_container(prefix, modifiers)
            elif token.value == "enum":
                self.parse_enum(prefix, modifiers)
            elif token.value in {"type", "function", "const", "let", "var"}:
                self.parse_statement(prefix, modifiers)
            else:
                start = self.index
                statement = self.collect_statement_or_block()
                if statement:
                    self.manual.append({
                        "class": "typescript_unparsed_declaration",
                        "symbol": prefix or "<root>",
                        "path": self.path,
                        "start_line": statement[0].line,
                        "end_line": statement[-1].line,
                        "signature": normalize_tokens(statement),
                    })
                elif self.index == start:
                    self.index += 1
        if closing:
            raise DenominatorError(f"unterminated TypeScript scope expecting {closing}")

    def declaration_name(self) -> Token:
        token = self.pop()
        if token.kind not in {"ident", "string"}:
            raise DenominatorError(f"expected TypeScript declaration name at line {token.line}")
        return token

    def qualify(self, prefix: str, name: str) -> str:
        clean = name.strip("\"'`")
        return f"{prefix}.{clean}" if prefix else clean

    def parse_namespace(self, prefix: str, modifiers: Sequence[Token]) -> None:
        keyword = self.pop()
        name = self.declaration_name()
        while self.peek() and self.peek().value != "{":
            self.pop()
        if not self.accept("{"):
            raise DenominatorError(f"namespace {name.value} has no body")
        symbol = self.qualify(prefix, name.value)
        self.items.append({
            "class": "typescript_namespace",
            "symbol": symbol,
            "signature": normalize_tokens([*modifiers, keyword, name]),
            "path": self.path,
            "start_line": (modifiers[0] if modifiers else keyword).line,
            "end_line": name.line,
        })
        self.parse_scope(symbol, "}")
        self.accept(";")

    def parse_container(self, prefix: str, modifiers: Sequence[Token]) -> None:
        keyword = self.pop()
        name = self.declaration_name()
        header = [*modifiers, keyword, name]
        angle = 0
        while self.peek():
            token = self.pop()
            if token.value == "<":
                angle += 1
            elif token.value == ">":
                angle = max(0, angle - 1)
            if token.value == "{" and angle == 0:
                break
            header.append(token)
        else:
            raise DenominatorError(f"{keyword.value} {name.value} has no body")
        symbol = self.qualify(prefix, name.value)
        self.items.append({
            "class": f"typescript_{keyword.value}",
            "symbol": symbol,
            "signature": normalize_tokens(header),
            "path": self.path,
            "start_line": header[0].line,
            "end_line": header[-1].line,
        })
        self.parse_members(symbol, keyword.value)
        self.accept(";")

    def parse_members(self, container: str, container_kind: str) -> None:
        while self.peek():
            if self.accept("}"):
                return
            start = self.index
            member = self.collect_member()
            if not member:
                if self.index == start:
                    self.index += 1
                continue
            signature = normalize_tokens(member)
            name = self.member_name(member)
            if not name:
                self.manual.append({
                    "class": "typescript_unparsed_member",
                    "symbol": container,
                    "path": self.path,
                    "start_line": member[0].line,
                    "end_line": member[-1].line,
                    "signature": signature,
                })
                continue
            self.items.append({
                "class": f"typescript_{container_kind}_member",
                "symbol": f"{container}.{name}",
                "signature": signature,
                "path": self.path,
                "start_line": member[0].line,
                "end_line": member[-1].line,
            })
        raise DenominatorError(f"unterminated TypeScript container {container}")

    def collect_member(self) -> list[Token]:
        result: list[Token] = []
        paren = bracket = brace = angle = 0
        while self.peek():
            token = self.peek()
            if token.value == "}" and paren == bracket == brace == angle == 0:
                break
            token = self.pop()
            result.append(token)
            if token.value == "(":
                paren += 1
            elif token.value == ")":
                paren = max(0, paren - 1)
            elif token.value == "[":
                bracket += 1
            elif token.value == "]":
                bracket = max(0, bracket - 1)
            elif token.value == "{":
                brace += 1
            elif token.value == "}":
                brace = max(0, brace - 1)
            elif token.value == "<":
                angle += 1
            elif token.value == ">":
                angle = max(0, angle - 1)
            if token.value == ";" and paren == bracket == brace == angle == 0:
                break
        return result

    @staticmethod
    def member_name(member: Sequence[Token]) -> str | None:
        filtered = [token for token in member if token.value not in {"readonly", "public", "private", "protected", "static", "abstract", "declare", "?"}]
        if not filtered:
            return None
        if filtered[0].value == "[":
            return "[index]"
        if filtered[0].value in {"new", "constructor"}:
            return filtered[0].value
        for token in filtered:
            if token.kind in {"ident", "string"}:
                return token.value.strip("\"'`")
        return None

    def parse_enum(self, prefix: str, modifiers: Sequence[Token]) -> None:
        keyword = self.pop()
        name = self.declaration_name()
        while self.peek() and self.peek().value != "{":
            self.pop()
        if not self.accept("{"):
            raise DenominatorError(f"enum {name.value} has no body")
        symbol = self.qualify(prefix, name.value)
        self.items.append({
            "class": "typescript_enum",
            "symbol": symbol,
            "signature": normalize_tokens([*modifiers, keyword, name]),
            "path": self.path,
            "start_line": (modifiers[0] if modifiers else keyword).line,
            "end_line": name.line,
        })
        while self.peek() and self.peek().value != "}":
            entry = self.collect_until({",", "}"})
            if entry:
                name_token = next((token for token in entry if token.kind in {"ident", "string", "number"}), None)
                if name_token:
                    clean_name = name_token.value.strip("\"'")
                    self.items.append({
                        "class": "typescript_enum_value",
                        "symbol": f"{symbol}.{clean_name}",
                        "signature": normalize_tokens(entry),
                        "path": self.path,
                        "start_line": entry[0].line,
                        "end_line": entry[-1].line,
                    })
                else:
                    self.manual.append({"class": "typescript_unparsed_enum_value", "symbol": symbol, "path": self.path, "start_line": entry[0].line, "end_line": entry[-1].line, "signature": normalize_tokens(entry)})
            self.accept(",")
        if not self.accept("}"):
            raise DenominatorError(f"unterminated enum {symbol}")
        self.accept(";")

    def parse_statement(self, prefix: str, modifiers: Sequence[Token]) -> None:
        keyword = self.pop()
        name = self.declaration_name()
        statement = [*modifiers, keyword, name, *self.collect_until({";"})]
        self.accept(";")
        symbol = self.qualify(prefix, name.value)
        class_name = {
            "type": "typescript_type_alias",
            "function": "typescript_function",
            "const": "typescript_constant",
            "let": "typescript_variable",
            "var": "typescript_variable",
        }[keyword.value]
        self.items.append({
            "class": class_name,
            "symbol": symbol,
            "signature": normalize_tokens(statement),
            "path": self.path,
            "start_line": statement[0].line,
            "end_line": statement[-1].line,
        })

    def collect_until(self, terminators: set[str]) -> list[Token]:
        result: list[Token] = []
        paren = bracket = brace = angle = 0
        while self.peek():
            token = self.peek()
            if token.value in terminators and paren == bracket == brace == angle == 0:
                break
            token = self.pop()
            result.append(token)
            if token.value == "(":
                paren += 1
            elif token.value == ")":
                paren = max(0, paren - 1)
            elif token.value == "[":
                bracket += 1
            elif token.value == "]":
                bracket = max(0, bracket - 1)
            elif token.value == "{":
                brace += 1
            elif token.value == "}":
                brace = max(0, brace - 1)
            elif token.value == "<":
                angle += 1
            elif token.value == ">":
                angle = max(0, angle - 1)
        return result

    def collect_statement_or_block(self) -> list[Token]:
        result: list[Token] = []
        brace = 0
        while self.peek():
            token = self.pop()
            result.append(token)
            if token.value == "{":
                brace += 1
            elif token.value == "}":
                if brace == 0:
                    self.index -= 1
                    result.pop()
                    break
                brace -= 1
            if token.value == ";" and brace == 0:
                break
            if brace == 0 and result and token.value == "}":
                break
        return result


def runtime_adapter_paths(nakama_root: Path) -> list[str]:
    paths = []
    for path in sorted((nakama_root / "server").glob("runtime*.go")):
        if not path.name.endswith("_test.go") and path.is_file():
            paths.append(path.relative_to(nakama_root).as_posix())
    if not paths:
        raise DenominatorError("no Nakama server runtime adapter files found")
    return paths


def adapter_file_items(root: Path, paths: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            "class": "runtime_adapter_file",
            "symbol": path,
            "signature": sha256((root / path).read_bytes()),
            "path": path,
            "start_line": 1,
            "end_line": (root / path).read_bytes().count(b"\n") + 1,
            "metadata": {},
        }
        for path in paths
    ]


def reconciliation(leaves: Sequence[dict[str, Any]]) -> dict[str, Any]:
    go_interfaces: dict[str, int] = {}
    ts_interfaces: dict[str, int] = {}
    for leaf in leaves:
        if leaf["class"] == "go_interface_method":
            parent = leaf["symbol"].rsplit(".", 1)[0].rsplit(".", 1)[-1]
            go_interfaces[parent] = go_interfaces.get(parent, 0) + 1
        elif leaf["class"] == "typescript_interface_member":
            parent = leaf["symbol"].rsplit(".", 1)[0].split(".")[-1]
            ts_interfaces[parent] = ts_interfaces.get(parent, 0) + 1
    shared = sorted(set(go_interfaces) & set(ts_interfaces))
    return {
        "schema": "trillionnium.runtime-language-surface-reconciliation.v1",
        "status": "candidate-unreviewed",
        "shared_interface_names": [
            {"name": name, "go_members": go_interfaces[name], "typescript_members": ts_interfaces[name]}
            for name in shared
        ],
        "go_only_interface_names": sorted(set(go_interfaces) - set(ts_interfaces)),
        "typescript_only_interface_names": sorted(set(ts_interfaces) - set(go_interfaces)),
        "semantic_equivalence_proven": False,
        "compatibility_credit": False,
    }


def generate(nakama_root: Path, common_root: Path, output_root: Path) -> dict[str, Any]:
    try:
        nakama_lock = verify_source_lock(nakama_root, repository=NAKAMA_REPOSITORY, revision=NAKAMA_COMMIT, tree=NAKAMA_TREE)
        common_lock = verify_source_lock(common_root, repository=COMMON_REPOSITORY, revision=COMMON_COMMIT, tree=COMMON_TREE)
    except SourceArchiveError as exc:
        raise DenominatorError(str(exc)) from exc

    common_paths = ["runtime/runtime.go", "runtime/config.go"]
    go_common = run_go_extractor(common_root, common_paths)
    adapter_paths = runtime_adapter_paths(nakama_root)
    go_adapters = run_go_extractor(nakama_root, adapter_paths, include_unexported=True)

    ts_path = "index.d.ts"
    ts_items, ts_manual = TypeScriptParser((common_root / ts_path).read_text(encoding="utf-8"), ts_path).parse()

    leaves: list[dict[str, Any]] = []
    for item in go_common["items"]:
        leaves.append(make_leaf(item, COMMON_REPOSITORY, COMMON_COMMIT, common_root))
    for item in ts_items:
        leaves.append(make_leaf(item, COMMON_REPOSITORY, COMMON_COMMIT, common_root))
    for item in adapter_file_items(nakama_root, adapter_paths):
        leaves.append(make_leaf(item, NAKAMA_REPOSITORY, NAKAMA_COMMIT, nakama_root))
    for item in go_adapters["items"]:
        item = {**item, "class": "runtime_adapter_" + item["class"]}
        leaves.append(make_leaf(item, NAKAMA_REPOSITORY, NAKAMA_COMMIT, nakama_root))

    leaves.sort(key=lambda item: item["id"])
    if len({leaf["id"] for leaf in leaves}) != len(leaves):
        raise DenominatorError("duplicate stable runtime leaf IDs")

    manual = []
    for source, entries in (
        ("common-go", go_common.get("manual_contracts", [])),
        ("server-adapters", go_adapters.get("manual_contracts", [])),
        ("typescript", ts_manual),
    ):
        manual.extend({"source": source, **entry} for entry in entries)
    manual.sort(key=canonical)

    counts: dict[str, int] = {}
    for leaf in leaves:
        counts[leaf["class"]] = counts.get(leaf["class"], 0) + 1

    manifest: dict[str, Any] = {
        "schema": "trillionnium.runtime-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "generator": {"name": GENERATOR, "version": VERSION},
        "denominator": "DEN-RUNTIME",
        "layer": "D4",
        "status": "candidate-unclassified",
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "unreviewed_count": len(leaves),
        "manual_contract_count": len(manual),
        "counts_by_class": counts,
        "source_locks": [nakama_lock, common_lock],
        "adapter_file_count": len(adapter_paths),
        "leaves": leaves,
        "manual_contracts": manual,
        "claims": {
            "sg1_complete": False,
            "runtime_semantic_equivalence": False,
            "go_plugin_abi_compatible": False,
            "compatibility_credit": False,
            "production_ready": False,
            "nakama_retired": False,
        },
    }
    manifest["content_sha256"] = sha256(canonical(manifest))
    reconcile = reconciliation(leaves)
    reconcile["runtime_manifest_sha256"] = manifest["content_sha256"]

    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "runtime-denominator.candidate.json", manifest)
    write_json(output_root / "runtime-language-reconciliation.candidate.json", reconcile)
    checksums = []
    for path in sorted(output_root.glob("*.json")):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (output_root / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return {
        "leaf_count": len(leaves),
        "manual_contract_count": len(manual),
        "adapter_file_count": len(adapter_paths),
        "sg1_complete": False,
        "compatibility_credit": False,
    }


def require_sg1(output_root: Path) -> None:
    manifest = json.loads((output_root / "runtime-denominator.candidate.json").read_text(encoding="utf-8"))
    failures = []
    if manifest.get("status") != "reviewed-locked":
        failures.append("status is not reviewed-locked")
    if manifest.get("unclassified_count") != 0:
        failures.append("unclassified runtime leaves remain")
    if manifest.get("unreviewed_count") != 0:
        failures.append("unreviewed runtime leaves remain")
    if manifest.get("manual_contract_count") != 0:
        failures.append("manual runtime contracts remain")
    if manifest.get("claims", {}).get("sg1_complete") is not True:
        failures.append("SG1 claim remains false")
    if failures:
        raise DenominatorError("SG1 remains open: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nakama-dir", type=Path, required=True)
    parser.add_argument("--common-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-sg1", action="store_true")
    args = parser.parse_args()
    try:
        summary = generate(args.nakama_dir.resolve(), args.common_dir.resolve(), args.output_dir.resolve())
        if args.require_sg1:
            require_sg1(args.output_dir.resolve())
        print(json.dumps(summary, sort_keys=True))
    except (DenominatorError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"runtime denominator generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
