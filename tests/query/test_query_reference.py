from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESERVED = set('+-=&|><!(){}[]^"~*?:\\/ ')


class QueryFailure(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    kind: str
    value: str = ""


def unescape(value: str) -> str:
    return value if value in RESERVED else "\\" + value


def suffix(text: str, index: int) -> tuple[str, int]:
    value = ""
    while index < len(text):
        character = text[index]
        if character == " ":
            return value or "1", index + 1
        if character == "\\":
            if index + 1 == len(text):
                return value or "1", len(text)
            value += unescape(text[index + 1])
            index += 2
            continue
        value += character
        index += 1
    return value or "1", index


def phrase(text: str, index: int) -> tuple[str, int]:
    value = ""
    escaped = False
    while index < len(text):
        character = text[index]
        if escaped:
            value += unescape(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return value, index + 1
        else:
            value += character
        index += 1
    raise QueryFailure("unterminated_query_quote")


def word(text: str, index: int, numeric: bool) -> tuple[str, bool, int]:
    value = ""
    seen_dot = False
    while index < len(text):
        character = text[index]
        if character == " " or character in ":^~":
            break
        if character == "\\":
            if index + 1 == len(text):
                break
            value += unescape(text[index + 1])
            numeric = False
            index += 2
            continue
        if numeric:
            if character.isascii() and character.isdigit():
                value += character
            elif character == "." and not seen_dot:
                seen_dot = True
                value += character
            else:
                numeric = False
                value += character
        else:
            value += character
        index += 1
    return value, numeric, index


def lex(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character == '"':
            value, index = phrase(text, index + 1)
            tokens.append(Token("phrase", value))
        elif character in "+-:><=":
            tokens.append(Token(character))
            index += 1
        elif character in "^~":
            value, index = suffix(text, index + 1)
            tokens.append(Token("boost" if character == "^" else "tilde", value))
        elif character == "\\":
            if index + 1 == len(text):
                raise QueryFailure("dangling_query_escape")
            value = unescape(text[index + 1])
            tail, _, index = word(text, index + 2, False)
            tokens.append(Token("string", value + tail))
        else:
            value, numeric, index = word(
                text,
                index,
                character.isascii() and character.isdigit(),
            )
            tokens.append(Token("number" if numeric else "string", value))
    return tokens


def number(value: str, reason: str) -> str:
    try:
        float(value)
    except ValueError as exc:
        raise QueryFailure(reason) from exc
    return value


def date(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QueryFailure("invalid_query_date") from exc
    if "T" not in value or not (value.endswith("Z") or "+" in value[10:] or "-" in value[10:]):
        raise QueryFailure("invalid_query_date")
    return value


def term(field: str, value: str) -> str:
    if value.startswith("/") and value.endswith("/"):
        if len(value) < 2:
            raise QueryFailure("invalid_query_regexp")
        kind = "regexp"
    elif "*" in value or "?" in value:
        kind = "wildcard"
    else:
        kind = "match"
    return f"term:{field}:{value}:{kind}"


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.index = 0

    def peek(self) -> Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise QueryFailure("invalid_query_syntax")
        self.index += 1
        return token

    def parse(self) -> str:
        clauses: list[str] = []
        while self.peek() is not None:
            clauses.append(self.clause())
        return ";".join(clauses)

    def clause(self) -> str:
        occurrence = "S"
        if self.peek() and self.peek().kind == "+":
            occurrence = "M"
            self.index += 1
        elif self.peek() and self.peek().kind == "-":
            occurrence = "N"
            self.index += 1
        expression = self.expression()
        boost = ""
        if self.peek() and self.peek().kind == "boost":
            boost = "^" + number(self.take().value, "invalid_query_boost")
        return occurrence + ":" + expression + boost

    def expression(self) -> str:
        token = self.take()
        if token.kind == "string":
            if self.peek() and self.peek().kind == ":":
                self.index += 1
                return self.field_expression(token.value)
            if self.peek() and self.peek().kind == "tilde":
                fuzzy = number(self.take().value, "invalid_query_fuzziness")
                return f"fuzzy::{token.value}:{fuzzy}"
            return term("", token.value)
        if token.kind == "number":
            return f"number::{number(token.value, 'invalid_query_number')}"
        if token.kind == "phrase":
            return f"phrase::{token.value}"
        raise QueryFailure("invalid_query_syntax")

    def field_expression(self, field: str) -> str:
        token = self.take()
        if token.kind == "string":
            if self.peek() and self.peek().kind == "tilde":
                fuzzy = number(self.take().value, "invalid_query_fuzziness")
                return f"fuzzy:{field}:{token.value}:{fuzzy}"
            return term(field, token.value)
        if token.kind == "number":
            return f"number:{field}:{number(token.value, 'invalid_query_number')}"
        if token.kind == "-":
            value = self.take()
            if value.kind != "number":
                raise QueryFailure("invalid_query_syntax")
            return f"number:{field}:{number('-' + value.value, 'invalid_query_number')}"
        if token.kind == "phrase":
            return f"phrase:{field}:{token.value}"
        if token.kind in {">", "<"}:
            return self.range(field, token.kind)
        raise QueryFailure("invalid_query_syntax")

    def range(self, field: str, operator: str) -> str:
        inclusive = bool(self.peek() and self.peek().kind == "=")
        if inclusive:
            self.index += 1
        comparison = {
            (">", False): "gt",
            (">", True): "ge",
            ("<", False): "lt",
            ("<", True): "le",
        }[(operator, inclusive)]
        token = self.take()
        if token.kind == "number":
            return f"nrange:{field}:{comparison}:{number(token.value, 'invalid_query_number')}"
        if token.kind == "-":
            value = self.take()
            if value.kind != "number":
                raise QueryFailure("invalid_query_syntax")
            parsed = number("-" + value.value, "invalid_query_number")
            return f"nrange:{field}:{comparison}:{parsed}"
        if token.kind == "phrase":
            return f"drange:{field}:{comparison}:{date(token.value)}"
        raise QueryFailure("invalid_query_syntax")


def fingerprint(text: str) -> str:
    if text == "":
        return "match_none"
    if text == "*":
        return "match_all"
    tokens = lex(text)
    if not tokens:
        raise QueryFailure("invalid_query_syntax")
    return Parser(tokens).parse()


class QueryReferenceTests(unittest.TestCase):
    def test_compatibility_vectors(self) -> None:
        document = json.loads(
            (ROOT / "contracts/query/query-compatibility-vectors.json").read_text()
        )
        self.assertFalse(any(document["claims"].values()))
        for case in document["accepted"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(fingerprint(case["input"]), case["fingerprint"])
        for case in document["rejected"]:
            with self.subTest(case=case["id"]):
                with self.assertRaisesRegex(QueryFailure, case["reason"]):
                    fingerprint(case["input"])


if __name__ == "__main__":
    unittest.main()
