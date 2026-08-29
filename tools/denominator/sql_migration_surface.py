# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


class SqlSurfaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Statement:
    text: str
    direction: str
    ordinal: int
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class Token:
    value: str
    kind: str


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def split_sections(text: str) -> list[tuple[str, str, int, int]]:
    marker = re.compile(r"^\s*--\s*\+migrate\s+(Up|Down)\s*$", re.I | re.M)
    matches = list(marker.finditer(text))
    if not matches:
        return [("unknown", text, 1, text.count("\n") + 1)]
    sections: list[tuple[str, str, int, int]] = []
    prefix = text[: matches[0].start()]
    if prefix.strip():
        sections.append(("preamble", prefix, 1, prefix.count("\n") + 1))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        start_line = text.count("\n", 0, start) + 1
        segment = text[start:end]
        sections.append(
            (
                match.group(1).lower(),
                segment,
                start_line,
                start_line + segment.count("\n"),
            )
        )
    return sections


def split_statements(text: str, direction: str, base_line: int) -> list[Statement]:
    statements: list[Statement] = []
    buffer: list[str] = []
    line = base_line
    start_line: int | None = None
    index = 0
    state = "normal"
    dollar_tag = ""
    block_depth = 0

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        raw = "".join(buffer).strip()
        if raw:
            statements.append(
                Statement(
                    text=raw,
                    direction=direction,
                    ordinal=len(statements) + 1,
                    start_line=start_line or end_line,
                    end_line=end_line,
                )
            )
        buffer = []
        start_line = None

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if start_line is None and not char.isspace():
            start_line = line

        if state == "line_comment":
            if char == "\n":
                state = "normal"
                buffer.append("\n")
                line += 1
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
                    buffer.append(" ")
                continue
            if char == "\n":
                line += 1
            index += 1
            continue
        if state == "single":
            buffer.append(char)
            if char == "'":
                if next_char == "'":
                    buffer.append(next_char)
                    index += 2
                    continue
                state = "normal"
            if char == "\n":
                line += 1
            index += 1
            continue
        if state == "double":
            buffer.append(char)
            if char == '"':
                if next_char == '"':
                    buffer.append(next_char)
                    index += 2
                    continue
                state = "normal"
            if char == "\n":
                line += 1
            index += 1
            continue
        if state == "dollar":
            if text.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                state = "normal"
                continue
            buffer.append(char)
            if char == "\n":
                line += 1
            index += 1
            continue

        if char == "-" and next_char == "-":
            state = "line_comment"
            index += 2
            continue
        if char == "/" and next_char == "*":
            state = "block_comment"
            block_depth = 1
            index += 2
            continue
        if char == "'":
            state = "single"
            buffer.append(char)
            index += 1
            continue
        if char == '"':
            state = "double"
            buffer.append(char)
            index += 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[index:])
            if match:
                dollar_tag = match.group(0)
                state = "dollar"
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if char == ";":
            flush(line)
            index += 1
            continue
        buffer.append(char)
        if char == "\n":
            line += 1
        index += 1

    if state in {"single", "double", "dollar", "block_comment"}:
        raise SqlSurfaceError(f"unterminated SQL lexical state: {state}")
    flush(line)
    return statements


def lex(sql: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if char in "'\"`":
            quote = char
            start = index
            index += 1
            while index < len(sql):
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            tokens.append(Token(sql[start:index], "quoted"))
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql[index:])
            if match:
                tag = match.group(0)
                end = sql.find(tag, index + len(tag))
                if end < 0:
                    raise SqlSurfaceError("unterminated dollar quote")
                end += len(tag)
                tokens.append(Token(sql[index:end], "dollar"))
                index = end
                continue
        if char.isalpha() or char == "_":
            start = index
            index += 1
            while index < len(sql) and (sql[index].isalnum() or sql[index] in "_$"):
                index += 1
            tokens.append(Token(sql[start:index], "word"))
            continue
        if char.isdigit():
            start = index
            index += 1
            while index < len(sql) and (sql[index].isalnum() or sql[index] in "._"):
                index += 1
            tokens.append(Token(sql[start:index], "number"))
            continue
        operator = sql[index : index + 2]
        if operator in {"::", ">=", "<=", "<>", "!=", "||", "->", "=>"}:
            tokens.append(Token(operator, "symbol"))
            index += 2
            continue
        tokens.append(Token(char, "symbol"))
        index += 1
    return tokens


def word_values(tokens: Sequence[Token]) -> list[str]:
    return [token.value.upper() if token.kind == "word" else token.value for token in tokens]


def unquote_identifier(value: str) -> str:
    if len(value) >= 2 and value[0] in '"`' and value[-1] == value[0]:
        return value[1:-1].replace(value[0] * 2, value[0])
    return value


def qualified_name(tokens: Sequence[Token], start: int) -> tuple[str, int]:
    parts: list[str] = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token.kind not in {"word", "quoted"}:
            break
        parts.append(unquote_identifier(token.value))
        index += 1
        if index < len(tokens) and tokens[index].value == ".":
            parts.append(".")
            index += 1
            continue
        break
    return "".join(parts), index


def skip_phrases(values: Sequence[str], start: int, phrases: Sequence[Sequence[str]]) -> int:
    index = start
    advanced = True
    while advanced:
        advanced = False
        for phrase in phrases:
            if list(values[index : index + len(phrase)]) == list(phrase):
                index += len(phrase)
                advanced = True
                break
    return index


def split_top_level(tokens: Sequence[Token], separator: str = ",") -> list[list[Token]]:
    output: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        if token.value == separator and depth == 0:
            output.append(current)
            current = []
        else:
            current.append(token)
    if current:
        output.append(current)
    return output


def parenthesized(tokens: Sequence[Token], open_index: int) -> tuple[list[Token], int]:
    depth = 0
    content: list[Token] = []
    for index in range(open_index, len(tokens)):
        token = tokens[index]
        if token.value == "(":
            depth += 1
            if depth > 1:
                content.append(token)
        elif token.value == ")":
            depth -= 1
            if depth == 0:
                return content, index + 1
            content.append(token)
        elif depth:
            content.append(token)
    raise SqlSurfaceError("unbalanced SQL parentheses")


def signature(tokens: Sequence[Token]) -> str:
    return normalize_whitespace(" ".join(token.value for token in tokens))


def _drop_names(tokens: Sequence[Token], start: int) -> list[str]:
    names: list[str] = []
    for part in split_top_level(tokens[start:]):
        while part and word_values(part[:1])[0] in {"CASCADE", "RESTRICT"}:
            part = part[1:]
        name, _ = qualified_name(part, 0)
        if name:
            names.append(name)
    return names


def classify_statement(
    statement: Statement, path: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tokens = lex(statement.text)
    values = word_values(tokens)
    items: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    base = {
        "path": path,
        "direction": statement.direction,
        "start_line": statement.start_line,
        "end_line": statement.end_line,
        "statement_ordinal": statement.ordinal,
    }

    def add(
        item_class: str,
        symbol: str,
        item_signature: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        items.append(
            {
                **base,
                "class": item_class,
                "symbol": symbol,
                "signature": item_signature or normalize_whitespace(statement.text),
                "metadata": metadata or {},
            }
        )

    if not tokens:
        return items, manual
    add(
        "sql_statement",
        f"{path}:{statement.direction}:{statement.ordinal}",
        metadata={"verb": values[0]},
    )

    if values[:2] == ["CREATE", "TABLE"]:
        index = skip_phrases(values, 2, [["IF", "NOT", "EXISTS"]])
        table, index = qualified_name(tokens, index)
        if not table:
            manual.append({**base, "class": "unparsed_create_table", "signature": normalize_whitespace(statement.text)})
            return items, manual
        add("db_table", table, metadata={"operation": "create"})
        try:
            open_index = next(i for i in range(index, len(tokens)) if tokens[i].value == "(")
            content, _ = parenthesized(tokens, open_index)
        except (StopIteration, SqlSurfaceError):
            manual.append({**base, "class": "unparsed_table_body", "symbol": table, "signature": normalize_whitespace(statement.text)})
            return items, manual
        for ordinal, entry in enumerate(split_top_level(content), 1):
            entry_values = word_values(entry)
            if not entry_values:
                continue
            if entry_values[0] in {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "EXCLUDE"}:
                name = f"{table}.constraint.{ordinal}"
                if entry_values[0] == "CONSTRAINT" and len(entry) > 1:
                    name = f"{table}.constraint.{unquote_identifier(entry[1].value)}"
                add("db_constraint", name, signature(entry), {"table": table, "constraint_kind": entry_values[0]})
                add("data_invariant_candidate", name, signature(entry), {"table": table, "invariant_kind": entry_values[0]})
                continue
            column = unquote_identifier(entry[0].value)
            column_symbol = f"{table}.{column}"
            add("db_column", column_symbol, signature(entry), {"table": table})
            joined = " " + " ".join(entry_values) + " "
            for phrase, kind in (
                ("NOT NULL", "not_null"),
                ("UNIQUE", "unique"),
                ("PRIMARY KEY", "primary_key"),
                ("CHECK", "check"),
                ("REFERENCES", "foreign_key"),
            ):
                if f" {phrase} " in joined:
                    symbol = f"{column_symbol}.{kind}"
                    add("db_inline_constraint", symbol, signature(entry), {"table": table, "column": column, "constraint_kind": kind})
                    add("data_invariant_candidate", symbol, signature(entry), {"table": table, "column": column, "invariant_kind": kind})
            if " DEFAULT " in joined:
                add("data_default_candidate", f"{column_symbol}.default", signature(entry), {"table": table, "column": column})
        return items, manual

    if values[:2] == ["ALTER", "TABLE"]:
        index = skip_phrases(values, 2, [["IF", "EXISTS"], ["ONLY"]])
        table, index = qualified_name(tokens, index)
        if not table:
            manual.append({**base, "class": "unparsed_alter_table", "signature": normalize_whitespace(statement.text)})
            return items, manual
        for ordinal, action in enumerate(split_top_level(tokens[index:]), 1):
            add(
                "db_alter_table_action",
                f"{table}.alter.{statement.ordinal}.{ordinal}",
                signature(action),
                {"table": table, "action": word_values(action)[0] if action else ""},
            )
        return items, manual

    if values[:2] == ["CREATE", "INDEX"] or values[:3] == ["CREATE", "UNIQUE", "INDEX"]:
        index = 2
        unique = False
        if values[:3] == ["CREATE", "UNIQUE", "INDEX"]:
            index = 3
            unique = True
        index = skip_phrases(values, index, [["CONCURRENTLY"], ["IF", "NOT", "EXISTS"]])
        name, index = qualified_name(tokens, index)
        table = ""
        try:
            on_index = values.index("ON", index)
            table, _ = qualified_name(tokens, on_index + 1)
        except ValueError:
            pass
        add("db_index", name or f"{path}.index.{statement.ordinal}", metadata={"table": table, "unique": str(unique).lower()})
        return items, manual

    drop_kinds = {"TABLE", "INDEX", "SEQUENCE", "TYPE", "VIEW"}
    if len(values) >= 2 and values[0] == "DROP" and values[1] in drop_kinds:
        kind = values[1].lower()
        index = skip_phrases(values, 2, [["IF", "EXISTS"]])
        names = _drop_names(tokens, index)
        if not names:
            names = [f"{path}.{kind}.{statement.ordinal}"]
        for name in names:
            add(f"db_drop_{kind}", name, metadata={"operation": "drop"})
        return items, manual

    create_kinds = {"SEQUENCE", "TYPE", "VIEW", "FUNCTION", "TRIGGER"}
    if len(values) >= 2 and values[0] == "CREATE" and values[1] in create_kinds:
        kind = values[1].lower()
        index = skip_phrases(values, 2, [["OR", "REPLACE"], ["IF", "NOT", "EXISTS"]])
        name, _ = qualified_name(tokens, index)
        add(f"db_{kind}", name or f"{path}.{kind}.{statement.ordinal}", metadata={"operation": "create"})
        return items, manual

    if values[0] in {"INSERT", "UPDATE", "DELETE"}:
        index = 1
        if values[0] == "INSERT" and index < len(values) and values[index] == "INTO":
            index += 1
        if values[0] == "DELETE" and index < len(values) and values[index] == "FROM":
            index += 1
        target, _ = qualified_name(tokens, index)
        add("data_backfill", target or f"{path}.data.{statement.ordinal}", metadata={"operation": values[0].lower()})
        return items, manual

    if values[0] in {"GRANT", "REVOKE"}:
        add("db_permission", f"{path}.{values[0].lower()}.{statement.ordinal}", metadata={"operation": values[0].lower()})
        return items, manual

    if values[0] in {"SET", "RESET", "BEGIN", "COMMIT", "ROLLBACK", "ANALYZE", "VACUUM", "SELECT"}:
        add("db_control_statement", f"{path}.{values[0].lower()}.{statement.ordinal}")
        return items, manual

    manual.append(
        {
            **base,
            "class": "unparsed_sql_statement",
            "symbol": f"{path}:{statement.direction}:{statement.ordinal}",
            "signature": normalize_whitespace(statement.text),
            "first_tokens": values[:8],
        }
    )
    return items, manual
