#!/usr/bin/env python3
"""Read-only engineering inventory; consistency success is never acceptance.

This is a narrow source-shape/documentation regression, not a Rust parser,
compatibility oracle, security verifier or replacement for evidence_admission.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 8 * 1024 * 1024
DIMENSIONS = (
    "scope-and-dependencies", "public-api-and-examples", "state-and-data",
    "errors-and-side-effects", "concurrency-and-recovery", "security-and-budgets",
    "tests-and-compatibility", "operations-and-change",
)
DEPTHS = {"overview", "partial-design", "detailed-bounded-design"}


class ValidationError(ValueError):
    """An inventory input or a source/document contract is inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def text(root: Path, path: str) -> str:
    candidate = Path(path)
    require(not candidate.is_absolute() and ".." not in candidate.parts,
            "inventory path must stay repository-relative")
    try:
        with (root / candidate).open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, f"input exceeds byte budget: {path}")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"unreadable UTF-8 inventory input: {path}") from error


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise ValidationError("non-finite JSON value")


def load(root: Path, path: str) -> dict[str, Any]:
    try:
        value = json.loads(text(root, path), object_pairs_hook=unique_object,
                           parse_constant=invalid_constant)
    except (ValueError, RecursionError) as error:
        raise ValidationError(f"invalid inventory JSON: {path}") from error
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def rows(value: Any, label: str) -> list[dict[str, Any]]:
    require(isinstance(value, list) and bool(value), f"nonempty {label} required")
    require(all(isinstance(row, dict) for row in value), f"invalid {label} row")
    identifiers = [row.get("id") for row in value]
    require(all(isinstance(item, str) and item.strip() == item and item
                for item in identifiers), f"invalid {label} identity")
    require(len(identifiers) == len(set(identifiers)), f"duplicate {label} identity")
    return value


def between(source: str, start: str, end: str) -> str:
    require(source.count(start) == 1 and source.count(end) == 1,
            "expected one recognized source/document block")
    begin = source.index(start) + len(start)
    finish = source.index(end)
    require(begin < finish, "reversed source/document block")
    return source[begin:finish]


def doc_block(document: str, name: str) -> str:
    return between(document, f"<!-- trnm-server-{name}:start -->",
                   f"<!-- trnm-server-{name}:end -->")


# This is a finite source inventory grammar, not a Rust compiler. Only owned
# top-level items, literal route constants and a particular dispatcher layout
# receive inventory entries. Unsupported or oversized layouts fail closed.
RUST_MAX_TOKENS = 131072
RUST_MAX_DEPTH = 64
RUST_MAX_RAW_HASHES = 255


class _RustToken:
    __slots__ = ("kind", "value", "start", "end")

    def __init__(self, kind: str, value: str, start: int, end: int) -> None:
        self.kind, self.value, self.start, self.end = kind, value, start, end


def _rust_identifier_start(char: str) -> bool:
    return char == "_" or (char.isidentifier() and not char.isdecimal())


def _rust_identifier_continue(char: str) -> bool:
    return char == "_" or ("a" + char).isidentifier()


def _rust_escape(source: str, position: int, *, byte: bool = False) -> tuple[str, int]:
    require(position < len(source), "unterminated Rust escape")
    char = source[position]
    simple = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"', "'": "'", "0": "\0"}
    if char in simple:
        return simple[char], position + 1
    if char == "x":
        value = source[position + 1:position + 3]
        require(len(value) == 2 and all(item in "0123456789abcdefABCDEF" for item in value),
                "invalid Rust hexadecimal escape")
        number = int(value, 16)
        require(byte or number <= 127, "non-ASCII Rust string hexadecimal escape")
        return chr(number), position + 3
    if char == "u" and not byte:
        require(source[position + 1:position + 2] == "{", "invalid Rust Unicode escape")
        end = source.find("}", position + 2, position + 11)
        require(end != -1, "invalid Rust Unicode escape")
        digits = source[position + 2:end].replace("_", "")
        require(1 <= len(digits) <= 6 and all(item in "0123456789abcdefABCDEF" for item in digits),
                "invalid Rust Unicode scalar")
        number = int(digits, 16)
        require(number <= 0x10ffff and not 0xd800 <= number <= 0xdfff,
                "invalid Rust Unicode scalar")
        return chr(number), end + 1
    if char == "\n" or source[position:position + 2] == "\r\n":
        position += 2 if char == "\r" else 1
        while position < len(source) and source[position] in " \t\n\r":
            position += 1
        return "", position
    raise ValidationError("unsupported Rust escape")


def _rust_tokens(source: str) -> tuple[list[_RustToken], dict[int, int]]:
    try:
        encoded_size = len(source.encode("utf-8"))
    except UnicodeError as error:
        raise ValidationError("Rust source must be valid UTF-8") from error
    require(encoded_size <= MAX_BYTES, "Rust source exceeds byte budget")
    tokens: list[_RustToken] = []
    groups: dict[int, int] = {}
    stack: list[int] = []
    position, size = 0, len(source)

    def emit(kind: str, value: str, begin: int, end: int) -> None:
        require(len(tokens) < RUST_MAX_TOKENS, "Rust source exceeds token budget")
        index = len(tokens)
        tokens.append(_RustToken(kind, value, begin, end))
        if kind == "punct" and value in ("(", "[", "{"):
            require(len(stack) < RUST_MAX_DEPTH, "Rust delimiter depth exceeded")
            stack.append(index)
        elif kind == "punct" and value in (")", "]", "}"):
            require(bool(stack) and tokens[stack[-1]].value == {")": "(", "]": "[", "}": "{"}[value],
                    "unbalanced Rust delimiter")
            opening = stack.pop()
            groups[opening], groups[index] = index, opening

    while position < size:
        char, begin = source[position], position
        if char.isspace():
            position += 1
            continue
        if source.startswith("//", position):
            end = source.find("\n", position + 2)
            position = size if end == -1 else end + 1
            continue
        if source.startswith("/*", position):
            position += 2
            depth = 1
            while depth and position < size:
                if source.startswith("/*", position):
                    depth += 1
                    require(depth <= RUST_MAX_DEPTH, "Rust comment depth exceeded")
                    position += 2
                elif source.startswith("*/", position):
                    depth -= 1
                    position += 2
                else:
                    position += 1
            require(depth == 0, "unterminated Rust block comment")
            continue
        # Raw, byte and C string contents are opaque to item/route scanning.
        # Byte/C literals are supported lexically, never accepted as &str routes.
        raw_prefix = next((prefix for prefix in ("br", "cr", "r")
                           if source.startswith(prefix, position)
                           and position + len(prefix) < size
                           and source[position + len(prefix)] in '#"'), None)
        if raw_prefix is not None:
            cursor = position + len(raw_prefix)
            while cursor < size and source[cursor] == "#":
                cursor += 1
            hashes = cursor - position - len(raw_prefix)
            require(hashes <= RUST_MAX_RAW_HASHES, "Rust raw delimiter budget exceeded")
            if cursor < size and source[cursor] == '"':
                marker = '"' + "#" * hashes
                end = source.find(marker, cursor + 1)
                require(end != -1, "unterminated Rust raw string")
                value = source[cursor + 1:end]
                kind = "string" if raw_prefix == "r" else "byte-string" if raw_prefix == "br" else "c-string"
                require(kind != "byte-string" or value.isascii(), "non-ASCII Rust raw byte string")
                require(kind != "c-string" or "\0" not in value, "NUL in Rust C string")
                position = end + len(marker)
                emit(kind, value, begin, position)
                continue
            require(raw_prefix == "r" and hashes == 1 and cursor < size
                    and _rust_identifier_start(source[cursor]), "unsupported Rust raw literal")
        prefix = source[position:position + 1] if source[position:position + 1] in ("b", "c") \
            and source[position + 1:position + 2] == '"' else ""
        if char == '"' or prefix:
            position += 1 + bool(prefix)
            value_parts: list[str] = []
            while position < size and source[position] != '"':
                if source[position] == "\\":
                    value, position = _rust_escape(source, position + 1, byte=prefix == "b")
                    value_parts.append(value)
                else:
                    require(source[position] != "\r", "bare carriage return in Rust string")
                    require(prefix != "b" or ord(source[position]) <= 127, "non-ASCII Rust byte string")
                    value_parts.append(source[position])
                    position += 1
            require(position < size, "unterminated Rust quoted string")
            position += 1
            value = "".join(value_parts)
            require(prefix != "c" or "\0" not in value, "NUL in Rust C string")
            emit("string" if not prefix else "byte-string" if prefix == "b" else "c-string",
                 value, begin, position)
            continue
        byte_char = source[position:position + 2] == "b'"
        if char == "'" or byte_char:
            cursor = position + 1 + bool(byte_char)
            require(cursor < size, "unterminated Rust character or lifetime")
            escaped = source[cursor] == "\\"
            if escaped:
                value, end = _rust_escape(source, cursor + 1, byte=byte_char)
            else:
                value, end = source[cursor], cursor + 1
            if source[end:end + 1] == "'":
                require(len(value) == 1 and (escaped or value not in "\n\r\t") and
                        (not byte_char or ord(value) <= (255 if escaped else 127)), "invalid Rust character")
                position = end + 1
                emit("char", value, begin, position)
                continue
            require(not byte_char and _rust_identifier_start(source[cursor]),
                    "unsupported Rust character or lifetime")
            end = cursor + 1
            while end < size and _rust_identifier_continue(source[end]):
                end += 1
            require(source[end:end + 1] != "'", "invalid Rust multi-character literal")
            emit("lifetime", source[cursor:end], begin, end)
            position = end
            continue
        if _rust_identifier_start(char):
            position += 1
            raw_identifier = char == "r" and source[position:position + 1] == "#"
            if raw_identifier:
                position += 1
                require(position < size and _rust_identifier_start(source[position]), "invalid Rust raw identifier")
            while position < size and _rust_identifier_continue(source[position]):
                position += 1
            emit("ident", source[begin + 2:position] if raw_identifier else source[begin:position], begin, position)
            continue
        if char.isdecimal() and char.isascii():
            position += 1
            while position < size and (source[position].isalnum() or source[position] == "_"):
                position += 1
            emit("number", source[begin:position], begin, position)
            continue
        require(char in "()[]{};:,.!#=<>+-*/%&|^?@$~", "unsupported Rust source character")
        operator = next((item for item in ("..=", "::", "=>", "->", "..", "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=")
                         if source.startswith(item, position)), char)
        position += len(operator)
        emit("punct", operator, begin, position)
    require(not stack, "unclosed Rust delimiter")
    return tokens, groups


def _rust_values(tokens: list[_RustToken], begin: int, end: int) -> list[str]:
    return [token.value if token.kind in ("ident", "number", "punct") else token.kind + ":" + token.value
            for token in tokens[begin:end]]


def _rust_attribute_name(tokens: list[_RustToken], groups: dict[int, int], opening: int) -> str:
    """Preserve paths; only a closed set of exact built-in shapes is safe.

    Unknown attributes may occur on unrelated opaque items. Their complete
    path/unsupported-shape marker cannot pass the controlled-item whitelists.
    This finite subset does not expand macros or resolve attribute names.
    """
    close = groups[opening]
    require(opening + 1 < close and tokens[opening + 1].kind == "ident",
            "unsupported Rust attribute name")
    path, cursor = [tokens[opening + 1].value], opening + 2
    while cursor < close and tokens[cursor].kind == "punct" and tokens[cursor].value == "::":
        require(cursor + 1 < close and tokens[cursor + 1].kind == "ident",
                "unsupported Rust attribute path")
        path.append(tokens[cursor + 1].value)
        cursor += 2
    name = "::".join(path)
    if len(path) != 1:
        return "qualified:" + name
    tail = tokens[cursor:close]

    def literal_value(part: list[_RustToken]) -> bool:
        return len(part) == 2 and part[0].kind == "punct" and part[0].value == "=" and part[1].kind == "string"

    def arguments() -> list[list[_RustToken]] | None:
        if (cursor >= close or tokens[cursor].kind != "punct" or tokens[cursor].value != "("
                or groups[cursor] != close - 1):
            return None
        parts, begin, index, finish = [], cursor + 1, cursor + 1, close - 1
        while index < finish:
            if tokens[index].kind == "punct" and tokens[index].value == ",":
                if index == begin:
                    return None
                parts.append(tokens[begin:index])
                begin = index + 1
            elif index in groups and groups[index] > index:
                index = groups[index]
            index += 1
        if begin < finish:
            parts.append(tokens[begin:finish])
        return parts

    parts = arguments()
    valid = False
    if name in ("cfg", "cfg_attr"):
        # Any conditional form is rejected on controlled items, irrespective of
        # the predicate. Only recognize a whole parenthesized attribute shape.
        valid = parts is not None and bool(parts)
    elif name in ("allow", "warn", "deny", "forbid"):
        valid = parts is not None and bool(parts)
        for index, part in enumerate(parts or []):
            if (len(part) == 3 and part[0].kind == "ident" and part[0].value == "reason"
                    and literal_value(part[1:])):
                valid &= index == len(parts) - 1 and index > 0
            else:
                valid &= bool(part) and len(part) % 2 == 1 and all(
                    token.kind == "ident" if offset % 2 == 0 else token.kind == "punct" and token.value == "::"
                    for offset, token in enumerate(part))
    elif name == "doc":
        valid = literal_value(tail) or parts is not None and len(parts) == 1 and (
            len(parts[0]) == 1 and parts[0][0].kind == "ident" and parts[0][0].value in ("hidden", "inline", "no_inline")
            or len(parts[0]) == 3 and parts[0][0].kind == "ident" and parts[0][0].value == "alias" and literal_value(parts[0][1:]))
    elif name == "deprecated":
        valid = not tail
        if parts:
            fields = [part[0].value if part and part[0].kind == "ident" else "" for part in parts]
            valid = len(fields) == len(set(fields)) and all(
                field in ("since", "note") and literal_value(part[1:]) for field, part in zip(fields, parts))
    elif name == "inline":
        valid = not tail or parts is not None and len(parts) == 1 and len(parts[0]) == 1 \
            and parts[0][0].kind == "ident" and parts[0][0].value in ("always", "never")
    elif name == "cold":
        valid = not tail
    elif name == "must_use":
        valid = not tail or literal_value(tail)
    else:
        return "unknown:" + name
    return name if valid else "unsupported-shape:" + name


def _rust_items(tokens: list[_RustToken], groups: dict[int, int], begin: int,
                end: int) -> list[dict[str, Any]]:
    """Walk sibling items; bodies/initializers stay opaque, never flatten scope."""
    result: list[dict[str, Any]] = []
    index = begin
    inner_attributes: list[str] = []
    while index < end:
        if tokens[index].kind == "punct" and tokens[index].value == ";":
            index += 1
            continue
        start, attributes = index, []
        while index < end and tokens[index].kind == "punct" and tokens[index].value == "#":
            index += 1
            inner = index < end and tokens[index].value == "!"
            index += bool(inner)
            require(index < end and tokens[index].value == "[" and index in groups,
                    "unsupported Rust item attribute")
            close = groups[index]
            name = _rust_attribute_name(tokens, groups, index)
            if inner:
                require(not result and not attributes, "unsupported late Rust inner attribute")
                inner_attributes.append(name)
            else:
                attributes.append(name)
            index = close + 1
        require(index < end, "Rust attribute without an item")
        if tokens[index].value == "pub":
            index += 1
            if index < end and tokens[index].value == "(":
                index = groups[index] + 1
        # Qualifiers are accepted only to reach an actual fn/impl item. They
        # cannot cause a const initializer, use tree or macro body to be visited.
        while index < end and tokens[index].value in ("unsafe", "async", "default"):
            index += 1
        require(index < end and tokens[index].kind == "ident", "unsupported Rust item head")
        kind, head = tokens[index].value, index
        if kind == "const" and index + 1 < end and tokens[index + 1].value == "fn":
            index += 1
            kind, head = "fn", index
        if kind == "extern":
            index += 1
            if index < end and tokens[index].kind == "string":
                index += 1
            if index < end and tokens[index].value == "fn":
                kind, head = "fn", index
        require(kind in ("use", "const", "static", "type", "fn", "impl", "struct", "enum", "mod", "trait", "extern", "union", "macro_rules")
                or tokens[index + 1:index + 2] and tokens[index + 1].value == "!",
                "unsupported top-level Rust item layout")
        name = tokens[head + 1].value if head + 1 < end and tokens[head + 1].kind == "ident" else None
        cursor, body = head + 1, None
        semicolon_item = kind in ("use", "const", "static", "type")
        while cursor < end:
            value = tokens[cursor].value
            if tokens[cursor].kind == "punct" and value == ";":
                finish = cursor + 1
                break
            if cursor in groups and groups[cursor] > cursor:
                close = groups[cursor]
                require(close < end, "Rust item escapes its enclosing scope")
                if value == "{" and not semicolon_item:
                    body, finish = cursor, close + 1
                    if finish < end and tokens[finish].value == ";":
                        finish += 1
                    break
                cursor = close + 1
            else:
                cursor += 1
        else:
            raise ValidationError("unterminated Rust item")
        result.append({"kind": kind, "name": name, "start": start, "head": head,
                       "end": finish, "body": body,
                       "attributes": inner_attributes + attributes})
        index = finish
    return result


def _rust_unconditional(item: dict[str, Any]) -> bool:
    return not any(name in ("cfg", "cfg_attr") for name in item["attributes"])


def _rust_safe_function_attributes(item: dict[str, Any]) -> bool:
    return all(name in {"allow", "warn", "deny", "forbid", "doc", "deprecated", "inline", "cold", "must_use"}
               for name in item["attributes"])


def _rust_production_modules(source: str) -> set[str]:
    tokens, groups = _rust_tokens(source)
    names = set()
    for item in _rust_items(tokens, groups, 0, len(tokens)):
        if item["kind"] != "mod" or not _rust_unconditional(item):
            continue
        controlled = item["name"] in {"storage_list_api", "auth_runtime", "legacy_http_api"}
        if controlled:
            require(item["body"] is None, "controlled route module must use an external declaration")
            require(all(name in {"allow", "warn", "deny", "forbid", "doc", "deprecated"}
                        for name in item["attributes"]), "unsupported controlled route module attribute")
        if item["body"] is None:
            names.add(item["name"])
    return names


def _rust_dispatcher(app: str) -> tuple[list[_RustToken], dict[int, int], list[dict[str, Any]], int, list[tuple[list[_RustToken], list[_RustToken]]]]:
    tokens, groups = _rust_tokens(app)
    items = _rust_items(tokens, groups, 0, len(tokens))
    methods = []
    for item in items:
        if item["kind"] == "fn":
            methods.append(item)
        elif (item["kind"] == "impl" and item["body"] is not None
              and _rust_unconditional(item) and _rust_safe_function_attributes(item)):
            methods.extend(_rust_items(tokens, groups, item["body"] + 1, groups[item["body"]]))
    handles = [item for item in methods if item["kind"] == "fn" and item["name"] == "handle_inner"
               and item["body"] is not None and _rust_unconditional(item)]
    require(len(handles) == 1, "expected one unconditional production handle_inner")
    handle = handles[0]
    require(_rust_safe_function_attributes(handle), "unsupported dispatcher function attribute")
    begin, end = handle["body"] + 1, groups[handle["body"]]
    inner_attributes, cursor = [], begin
    while cursor < end and tokens[cursor].kind == "punct" and tokens[cursor].value == "#":
        if cursor + 1 >= end or tokens[cursor + 1].kind != "punct" or tokens[cursor + 1].value != "!":
            break
        opening = cursor + 2
        require(opening < end and tokens[opening].kind == "punct" and tokens[opening].value == "["
                and opening in groups and groups[opening] < end, "unsupported dispatcher inner attribute")
        inner_attributes.append(_rust_attribute_name(tokens, groups, opening))
        cursor = groups[opening] + 1
    body_attributes = {"attributes": inner_attributes}
    require(_rust_unconditional(body_attributes), "dispatcher inner attributes cannot be conditional")
    require(_rust_safe_function_attributes(body_attributes), "unsupported dispatcher inner attribute")
    begin = cursor
    marker = ["let", "response", "=", "match", "(", "request", ".", "method", ".", "as_str", "(", ")", ",",
              "request", ".", "target", ".", "as_str", "(", ")", ")", "{"]
    candidates, cursor = [], begin
    while cursor < end:
        if (_rust_values(tokens, cursor, cursor + len(marker)) == marker
                and all(token.kind != "string" for token in tokens[cursor:cursor + len(marker)])):
            candidates.append(cursor)
        cursor = groups[cursor] + 1 if cursor in groups and groups[cursor] > cursor else cursor + 1
    require(len(candidates) == 1, "expected one recognized structural dispatcher")
    dispatch = candidates[0]
    opening, closing = dispatch + len(marker) - 1, groups[dispatch + len(marker) - 1]
    require(_rust_values(tokens, closing + 1, closing + 6) == [";", "if", "response", ".", "status"],
            "dispatcher completion layout changed")
    arms, cursor = [], opening + 1
    while cursor < closing:
        start = cursor
        while cursor < closing and tokens[cursor].value != "=>":
            if cursor in groups and groups[cursor] > cursor:
                cursor = groups[cursor] + 1
            else:
                cursor += 1
        require(cursor < closing, "unsupported dispatcher pattern")
        pattern, cursor = tokens[start:cursor], cursor + 1
        body_start = cursor
        if cursor < closing and tokens[cursor].value == "{":
            cursor = groups[cursor] + 1
        else:
            while cursor < closing and tokens[cursor].value != ",":
                cursor = groups[cursor] + 1 if cursor in groups and groups[cursor] > cursor else cursor + 1
        arms.append((pattern, tokens[body_start:cursor]))
        if cursor < closing and tokens[cursor].value == ",":
            cursor += 1
    return tokens, groups, items, dispatch, arms


def _rust_pattern_values(pattern: list[_RustToken]) -> list[str]:
    # Literal values retain a separate kind: identifiers cannot substitute for
    # the pinned strings even when their spelling happens to match a template.
    return ["S:" + token.value if token.kind == "string" else token.value
            if token.kind in ("ident", "punct", "number") else token.kind + ":" + token.value for token in pattern]


def _rust_literal_routes(arms: list[tuple[list[_RustToken], list[_RustToken]]]) -> list[tuple[str, str]]:
    routes = []
    for pattern, _ in arms:
        # Only literal alternatives before a guard are route patterns. A tuple
        # appearing inside a guard expression does not advertise a route.
        values = [token.value for token in pattern]
        if "if" in values:
            pattern = pattern[:values.index("if")]
        cursor = 0
        while cursor < len(pattern):
            part = pattern[cursor:cursor + 5]
            if (len(part) == 5 and all(token.kind == "punct" for token in (part[0], part[2], part[4]))
                    and [token.value for token in (part[0], part[2], part[4])] == ["(", ",", ")"]
                    and part[1].kind == part[3].kind == "string"
                    and part[1].value in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                    and part[3].value.startswith("/") and not any(char.isspace() for char in part[3].value)):
                routes.append((part[1].value, part[3].value))
            cursor += 5
            if cursor < len(pattern):
                if pattern[cursor].value != "|":
                    break
                cursor += 1
    return routes


def _rust_list_routes(tokens: list[_RustToken], groups: dict[int, int], items: list[dict[str, Any]],
                      dispatch: int, arms: list[tuple[list[_RustToken], list[_RustToken]]]) -> list[tuple[str, str]]:
    declarations = [item for item in items if item["kind"] == "const" and item["name"] == "STORAGE_LIST_ROUTES"]
    require(len(declarations) == 1, "integrated storage list needs one top-level route constant")
    item = declarations[0]
    require(_rust_unconditional(item), "storage list route constant cannot be conditional")
    require(all(name in {"allow", "warn", "deny", "forbid", "doc", "deprecated"} for name in item["attributes"]),
            "unsupported storage route constant attribute")
    require(item["end"] <= dispatch, "storage list route constant must precede the dispatcher")
    values = _rust_values(tokens, item["head"], item["end"])
    annotation = ["const", "STORAGE_LIST_ROUTES", ":", "[", "(", "&", "str", ",", "&", "str", ")", ";", "2", "]", "=", "["]
    require(values[:len(annotation)] == annotation, "unsupported storage list literal type or initializer")
    cursor, routes = item["head"] + len(annotation), []
    initializer_close = groups[cursor - 1]
    while cursor < initializer_close:
        part = tokens[cursor:cursor + 5]
        require(len(part) == 5 and all(token.kind == "punct" for token in (part[0], part[2], part[4]))
                and [part[0].value, part[2].value, part[4].value] == ["(", ",", ")"]
                and part[1].kind == part[3].kind == "string",
                "storage list entries must be literal string pairs")
        routes.append((part[1].value, part[3].value))
        cursor += 5
        if cursor < initializer_close:
            require(tokens[cursor].value == ",", "storage list pair separator missing")
            cursor += 1
    require(cursor == initializer_close and _rust_values(tokens, initializer_close, item["end"]) == ["]", ";"],
            "storage list initializer has an expression suffix")
    require(len(routes) == 2 and set(routes) == {("GET", "/v2/storage/{collection}"),
                ("GET", "/v2/storage/{collection}/{user_id}")},
            "storage list route constant differs from the two pinned templates")
    guarded = False
    for pattern, _ in arms:
        values = _rust_pattern_values(pattern)
        prefix, suffix = ["(", "S:GET", ",", "target", ")", "if"], ["is_list_target", "(", "target", ")"]
        if values[:6] == prefix and values[-4:] == suffix:
            middle = pattern[6:-4]
            guarded |= len(middle) % 2 == 0 and all(
                token.kind == "ident" if index % 2 == 0 else token.value == "::"
                for index, token in enumerate(middle))
    require(guarded, "storage list route constant needs the integrated guarded dispatcher")
    return routes


def source_interface(config: str, app: str, *, list_integrated: bool = False,
                     legacy_http_source: str | None = None) -> dict[str, Any]:
    # These owned regions deliberately exclude unit fixtures and unrelated
    # routing helpers. A source layout change requires reviewing this extractor.
    command_region = between(config, "let command = match arguments {",
                             "\n        let bind =")
    commands = re.findall(
        r'^\s*\[_, value\] if value == "([a-z-]+)" => Command::[A-Za-z]+,\s*$',
        command_region, re.MULTILINE,
    )
    tokens, groups, items, dispatch, arms = _rust_dispatcher(app)
    routes = _rust_literal_routes(arms)
    if list_integrated:
        routes.extend(_rust_list_routes(tokens, groups, items, dispatch, arms))
    if legacy_http_source is not None:
        production_http = legacy_http_source.split("\n#[cfg(test)]", 1)[0]
        legacy_region = between(production_http,
            "pub fn legacy_auth_http_route(method: &str, target: &str)",
            "\n/// Local caps")
        require('if method != "POST"' in legacy_region
                and "target.split_once('?').map_or(target, |(path, _)| path)" in legacy_region,
                "legacy route recognition must retain POST and query binding")
        legacy_routes = re.findall(r'"(/[^"\s]+)" => Some\(LegacyAuthHttpRoute::[A-Za-z]+\)', legacy_region)
        require(len(legacy_routes) == 3 and set(legacy_routes) == {
            "/v2/account/authenticate/device", "/v2/account/session/refresh", "/v2/session/logout"},
            "legacy route source must be the closed three-path set")
        guarded = [body for pattern, body in arms if _rust_pattern_values(pattern) == [
            "(", "S:POST", ",", "target", ")", "if", "legacy_auth_http_route", "(",
            "S:POST", ",", "target", ")", ".", "is_some", "(", ")"]]
        require(len(guarded) == 1, "legacy closed route recognizer needs the actual guarded dispatcher")
        call = [".", "legacy_request", "(", "&", "mut", "self", ".", "repository", ",", "request", ",", "route", ")"]
        body = guarded[0]
        require(any(_rust_values(body, index, index + len(call)) == call
                    and all(token.kind != "string" for token in body[index:index + len(call)])
                    for index in range(len(body) - len(call) + 1)),
                "legacy dispatcher must call the selected authority")
        routes.extend(("POST", path) for path in legacy_routes)
    require(commands and len(commands) == len(set(commands)), "missing or duplicate CLI arms")
    require(routes and len(routes) == len(set(routes)), "missing or duplicate route arms")
    production_config = config.split("\n#[cfg(test)]", 1)[0]
    environment = sorted(set(re.findall(r'"(TRNM_SERVER_[A-Z0-9_]+)"', production_config)))
    require(bool(environment), "missing configuration names")
    return {"commands": sorted(commands), "routes": sorted(routes),
            "environment_names": environment}


def check_documented_interface(interface: dict[str, Any], document: str) -> None:
    commands = re.findall(r'^`([a-z-]+)`\s*$', doc_block(document, "cli"), re.MULTILINE)
    routes = re.findall(r'^\| `(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)` \| `(/[^`]+)` \|',
                        doc_block(document, "routes"), re.MULTILINE)
    environment = re.findall(r'^\| `(TRNM_SERVER_[A-Z0-9_]+)` \|',
                             doc_block(document, "config"), re.MULTILINE)
    require(sorted(commands) == interface["commands"], "documented CLI differs from source")
    require(sorted(routes) == interface["routes"], "documented routes differ from source")
    require(sorted(environment) == interface["environment_names"],
            "documented configuration names differ from source")


def inspect(root: Path) -> dict[str, Any]:
    registry = load(root, "docs/status/MODULE_DOCUMENTATION.json")
    modules = rows(registry.get("modules"), "modules")
    discovered = {path.parent.name for path in (root / "crates").glob("*/Cargo.toml")}
    require({row["id"] for row in modules} == discovered,
            "module registry differs from actual Cargo package directories")
    depth = load(root, "docs/status/DOCUMENTATION_DEPTH.json")
    require(depth.get("schema") == "trillionnium.documentation-depth.v1", "unexpected depth schema")
    require(depth.get("project_id") == "trillionnium-game", "unexpected depth project")
    require(depth.get("assessment_kind") == "engineering-review-proposal", "unexpected assessment kind")
    require(depth.get("claim_credit") is False, "depth assessment cannot grant credit")
    require(depth.get("required_design_dimensions") == list(DIMENSIONS), "design dimensions changed")
    assessments = rows(depth.get("modules"), "depth assessments")
    require({row["id"] for row in assessments} == discovered,
            "depth assessment does not cover every package exactly once")
    for row in assessments:
        require(row.get("depth") in DEPTHS, "invalid documentation depth")
        gaps = row.get("remaining_design_work")
        require(isinstance(gaps, list) and bool(gaps)
                and all(isinstance(item, str) and item.strip() for item in gaps),
                "remaining design work must be explicit")
    for row in modules:
        require(row.get("documentation") == f"crates/{row['id']}/README.md",
                "noncanonical module README")
        require(bool(text(root, row["documentation"]).strip()), "empty module README")
    component_registry = load(root, "docs/status/COMPONENT_DOCUMENTATION.json")
    components = rows(component_registry.get("components"), "components")
    component_depth = rows(depth.get("components"), "component assessments")
    require({row["id"] for row in components} == {row["id"] for row in component_depth},
            "component depth assessment coverage differs")
    for row in component_depth:
        require(isinstance(row.get("remaining_design_work"), list)
                and bool(row["remaining_design_work"])
                and all(isinstance(item, str) and item.strip() for item in row["remaining_design_work"]),
                "component remaining design work must be explicit")
    module_path = "crates/trnm-server/src/runtime/mod.rs"
    module_source = text(root, module_path) if (root / module_path).is_file() else ""
    production_modules = _rust_production_modules(module_source)
    list_integrated = "storage_list_api" in production_modules
    interface = source_interface(
        text(root, "crates/trnm-server/src/runtime/config.rs"),
        text(root, "crates/trnm-server/src/runtime/app.rs"),
        list_integrated=list_integrated,
        legacy_http_source=(text(root, "crates/trnm-server/src/runtime/legacy_http_api.rs")
            if {"auth_runtime", "legacy_http_api"} <= production_modules
            else None),
    )
    check_documented_interface(interface, text(root, "docs/DEVELOPMENT.md"))
    gaps = rows(load(root, "docs/status/GAP_REGISTER.json").get("gaps"), "gaps")
    for row in gaps:
        require(isinstance(row.get("status"), str) and bool(row["status"]), "gap status missing")
        require(isinstance(row.get("close_criteria"), list) and bool(row["close_criteria"]),
                "gap close criteria missing")
        require(isinstance(row.get("required_evidence_types"), list)
                and bool(row["required_evidence_types"]), "gap evidence requirements missing")
    detail = load(root, "docs/roadmap/ENGINEERING_EXIT_CONTRACTS.json")
    require(detail.get("schema") == "trillionnium.engineering-exit-detail.v1",
            "unexpected engineering exit-detail schema")
    require(detail.get("claim_credit") is False, "exit detail cannot grant credit")
    gap_details = rows(detail.get("gap_details"), "gap details")
    require({row["id"] for row in gap_details} == {row["id"] for row in gaps},
            "engineering detail must cover every registered gap exactly once")
    for row in gap_details:
        require(type(row.get("priority_band")) is int and row["priority_band"] in (0, 1, 2),
                "invalid advisory priority band")
        for field in ("implementation_steps", "required_checks", "external_obligations"):
            require(isinstance(row.get(field), list) and bool(row[field])
                    and all(isinstance(item, str) and item.strip() for item in row[field]),
                    f"gap detail requires nonempty {field}")
    domains = detail.get("domain_details")
    require(isinstance(domains, list) and len(domains) == 11
            and all(isinstance(row, dict) and type(row.get("issue")) is int for row in domains)
            and {row["issue"] for row in domains} == set(range(137, 148)),
            "full-surface engineering detail must retain all eleven work packages")
    return {
        "schema": "trillionnium.engineering-readiness-report.v1",
        "scope": "read-only source and documentation inventory; not evidence admission",
        "module_count": len(modules),
        "documented_module_count": len(modules),
        "documentation_depth_counts": dict(sorted(Counter(row["depth"] for row in assessments).items())),
        "design_acceptance": "not-evaluated; requires independent review",
        "component_count": len(components),
        "server_interface": interface,
        "gap_count": len(gaps),
        "gap_detail_count": len(gap_details),
        "full_surface_work_package_count": len(domains),
        "recorded_gap_status_counts": dict(sorted(Counter(row["status"] for row in gaps).items())),
        "gaps": [{key: row.get(key) for key in
                  ("id", "status", "owner_role", "external_dependency", "close_criteria",
                   "required_evidence_types", "evidence_ids")} for row in gaps],
        "closure_validated": False,
        "claim_credit": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = inspect(args.root)
    except ValidationError as error:
        print(f"engineering-readiness: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
