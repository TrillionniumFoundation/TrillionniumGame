#!/usr/bin/env python3
"""Generate fail-closed API and realtime parity-denominator candidates.

The generator is intentionally standard-library-only. Network access is used only
by ``--fetch`` to obtain four files at immutable commits. Generation itself is
offline and rejects any source whose Git blob identity differs from the reviewed
TrillionniumGame upstream baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

GENERATOR = "trillionniumgame-api-rtapi-denominator"
VERSION = "0.1.0"

SOURCES: dict[str, dict[str, str]] = {
    "nakama-apigrpc-proto": {
        "repository": "heroiclabs/nakama",
        "commit": "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09",
        "path": "apigrpc/apigrpc.proto",
        "blob": "1cc63aae1aaa5dc56ede9c9d0b6f9a95ff91361c",
    },
    "nakama-apigrpc-swagger": {
        "repository": "heroiclabs/nakama",
        "commit": "d4d92f93f78bbbe62c7fc50a3f85c772ec121a09",
        "path": "apigrpc/apigrpc.swagger.json",
        "blob": "17dc459faa529b39278fead44fb4abafe786ccd9",
    },
    "common-api-proto": {
        "repository": "heroiclabs/nakama-common",
        "commit": "449b77ecc8789aa466c36b67f6e498033dfcd9c5",
        "path": "api/api.proto",
        "blob": "ddd2744739a252c268b2be004ff0e45c498adb35",
    },
    "common-rtapi-proto": {
        "repository": "heroiclabs/nakama-common",
        "commit": "449b77ecc8789aa466c36b67f6e498033dfcd9c5",
        "path": "rtapi/realtime.proto",
        "blob": "b23efef88565e0e09b3f6ee7ed8e08e9d240e27d",
    },
}


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Token:
    value: str
    kind: str
    line: int


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def git_blob_sha(value: bytes) -> str:
    return hashlib.sha1(f"blob {len(value)}\0".encode("ascii") + value).hexdigest()  # noqa: S324


def leaf_id(layer: str, item_class: str, symbol: str) -> str:
    digest = hashlib.sha256(f"{layer}\0{item_class}\0{symbol}".encode()).hexdigest()[:16]
    return f"TG-{layer}-{digest.upper()}"


def line_count(value: bytes) -> int:
    return value.count(b"\n") + (0 if not value or value.endswith(b"\n") else 1)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def fetch_sources(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for key, source in SOURCES.items():
        url = (
            f"https://raw.githubusercontent.com/{source['repository']}/"
            f"{source['commit']}/{source['path']}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": GENERATOR})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                data = response.read(8 * 1024 * 1024 + 1)
        except OSError as exc:
            raise ContractError(f"could not fetch {key}: {exc}") from exc
        if len(data) > 8 * 1024 * 1024:
            raise ContractError(f"source exceeds 8 MiB: {key}")
        actual = git_blob_sha(data)
        if actual != source["blob"]:
            raise ContractError(
                f"source identity mismatch for {key}: expected {source['blob']}, got {actual}"
            )
        destination = root / source["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def load_sources(root: Path) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    loaded: dict[str, bytes] = {}
    lock: list[dict[str, Any]] = []
    for key, source in sorted(SOURCES.items()):
        path = root / source["path"]
        if not path.is_file():
            raise ContractError(f"missing pinned source: {path}")
        data = path.read_bytes()
        actual = git_blob_sha(data)
        if actual != source["blob"]:
            raise ContractError(
                f"source identity mismatch for {key}: expected {source['blob']}, got {actual}"
            )
        loaded[key] = data
        lock.append(
            {
                **source,
                "key": key,
                "size": len(data),
                "lines": line_count(data),
                "sha256": sha256(data),
            }
        )
    return loaded, lock


def lex_proto(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    line = 1
    while i < len(text):
        char = text[i]
        if char in " \t\r":
            i += 1
        elif char == "\n":
            line += 1
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ContractError(f"unterminated Proto comment at line {line}")
            line += text.count("\n", i, end + 2)
            i = end + 2
        elif char in {'"', "'"}:
            quote, start, start_line = char, i, line
            i += 1
            escaped = False
            while i < len(text):
                current = text[i]
                if current == "\n":
                    line += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    i += 1
                    break
                i += 1
            else:
                raise ContractError(f"unterminated Proto string at line {start_line}")
            tokens.append(Token(text[start:i], "string", start_line))
        elif char.isalpha() or char == "_":
            start = i
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] == "_"):
                i += 1
            tokens.append(Token(text[start:i], "ident", line))
        elif char.isdigit():
            start = i
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] in "xX"):
                i += 1
            tokens.append(Token(text[start:i], "number", line))
        else:
            tokens.append(Token(char, "symbol", line))
            i += 1
    return tokens


def unquote(value: str) -> str:
    body = value[1:-1]
    return body.replace("\\/", "/").replace('\\"', '"').replace("\\\\", "\\")


class ProtoParser:
    def __init__(self, text: str):
        self.tokens = lex_proto(text)
        self.i = 0
        self.package = ""
        self.items: list[dict[str, Any]] = []
        self.manual: list[dict[str, Any]] = []

    def peek(self, offset: int = 0) -> Token | None:
        index = self.i + offset
        return self.tokens[index] if index < len(self.tokens) else None

    def pop(self) -> Token:
        token = self.peek()
        if token is None:
            raise ContractError("unexpected end of Proto source")
        self.i += 1
        return token

    def accept(self, value: str) -> Token | None:
        token = self.peek()
        if token and token.value == value:
            self.i += 1
            return token
        return None

    def expect(self, value: str) -> Token:
        token = self.pop()
        if token.value != value:
            raise ContractError(f"expected {value!r} at line {token.line}, got {token.value!r}")
        return token

    def ident(self) -> Token:
        token = self.pop()
        if token.kind != "ident":
            raise ContractError(f"expected identifier at line {token.line}")
        return token

    def qualified(self, parent: str, name: str) -> str:
        return ".".join(part for part in (self.package, parent, name) if part)

    def parse(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        while self.peek():
            value = self.peek().value
            if value == "package":
                self.parse_package()
            elif value == "message":
                self.parse_message("")
            elif value == "enum":
                self.parse_enum("")
            elif value == "service":
                self.parse_service()
            else:
                self.skip_declaration()
        return self.items, self.manual

    def parse_package(self) -> None:
        self.expect("package")
        parts: list[str] = []
        while self.peek() and self.peek().value != ";":
            parts.append(self.pop().value)
        self.expect(";")
        self.package = "".join(parts)

    def parse_message(self, parent: str) -> None:
        start = self.expect("message")
        name = self.ident().value
        full = self.qualified(parent, name)
        message = {"class": "proto_message", "symbol": full, "start": start.line, "end": start.line}
        self.items.append(message)
        self.expect("{")
        child_parent = ".".join(part for part in (parent, name) if part)
        while self.peek() and self.peek().value != "}":
            if self.peek().value == "message":
                self.parse_message(child_parent)
            elif self.peek().value == "enum":
                self.parse_enum(child_parent)
            elif self.peek().value == "oneof":
                self.parse_oneof(full)
            elif self.peek().value in {"option", "reserved", "extensions", "extend"}:
                self.skip_declaration()
            else:
                statement = self.collect_statement()
                field = self.parse_field(statement, full, None)
                if field:
                    self.items.append(field)
                elif statement:
                    self.record_manual("message_statement", full, statement)
        message["end"] = self.expect("}").line
        self.accept(";")

    def parse_oneof(self, message: str) -> None:
        self.expect("oneof")
        name = self.ident().value
        self.expect("{")
        while self.peek() and self.peek().value != "}":
            if self.peek().value == "option":
                self.skip_declaration()
                continue
            statement = self.collect_statement()
            field = self.parse_field(statement, message, name)
            if field:
                self.items.append(field)
            elif statement:
                self.record_manual("oneof_statement", f"{message}.{name}", statement)
        self.expect("}")
        self.accept(";")

    def parse_enum(self, parent: str) -> None:
        start = self.expect("enum")
        name = self.ident().value
        full = self.qualified(parent, name)
        enum_item = {"class": "proto_enum", "symbol": full, "start": start.line, "end": start.line}
        self.items.append(enum_item)
        self.expect("{")
        while self.peek() and self.peek().value != "}":
            if self.peek().value in {"option", "reserved"}:
                self.skip_declaration()
                continue
            statement = self.collect_statement()
            values = [token.value for token in statement]
            if "=" not in values:
                self.record_manual("enum_statement", full, statement)
                continue
            equals = values.index("=")
            try:
                number = int(values[equals + 1], 0)
            except (ValueError, IndexError):
                self.record_manual("enum_statement", full, statement)
                continue
            value_name = values[0]
            self.items.append(
                {
                    "class": "proto_enum_value",
                    "symbol": f"{full}.{value_name}",
                    "number": number,
                    "start": statement[0].line,
                    "end": statement[-1].line,
                }
            )
        enum_item["end"] = self.expect("}").line
        self.accept(";")

    def parse_service(self) -> None:
        start = self.expect("service")
        name = self.ident().value
        full = self.qualified("", name)
        service = {"class": "grpc_service", "symbol": full, "start": start.line, "end": start.line}
        self.items.append(service)
        self.expect("{")
        while self.peek() and self.peek().value != "}":
            if self.peek().value == "rpc":
                self.parse_rpc(full)
            else:
                self.skip_declaration()
        service["end"] = self.expect("}").line
        self.accept(";")

    def parse_rpc(self, service: str) -> None:
        start = self.expect("rpc")
        name = self.ident().value
        self.expect("(")
        client_stream = bool(self.accept("stream"))
        input_type = self.parenthesized_type()
        self.expect("returns")
        self.expect("(")
        server_stream = bool(self.accept("stream"))
        output_type = self.parenthesized_type()
        body: list[Token] = []
        if self.accept(";"):
            end = self.tokens[self.i - 1].line
        else:
            self.expect("{")
            depth = 1
            while self.peek() and depth:
                token = self.pop()
                if token.value == "{":
                    depth += 1
                elif token.value == "}":
                    depth -= 1
                if depth:
                    body.append(token)
            if depth:
                raise ContractError(f"unterminated RPC {service}.{name}")
            end = self.tokens[self.i - 1].line
            self.accept(";")
        symbol = f"{service}.{name}"
        self.items.append(
            {
                "class": "grpc_method",
                "symbol": symbol,
                "input": input_type,
                "output": output_type,
                "client_streaming": client_stream,
                "server_streaming": server_stream,
                "start": start.line,
                "end": end,
            }
        )
        for binding in extract_http_bindings(body):
            self.items.append(
                {
                    "class": "proto_http_binding",
                    "symbol": f"{symbol}:{binding['method']} {binding['path']}",
                    **binding,
                }
            )

    def parenthesized_type(self) -> str:
        depth, parts = 1, []
        while self.peek() and depth:
            token = self.pop()
            if token.value == "(":
                depth += 1
            elif token.value == ")":
                depth -= 1
            if depth:
                parts.append(token.value)
        if depth:
            raise ContractError("unterminated parenthesized type")
        return join_type(parts)

    def collect_statement(self) -> list[Token]:
        result: list[Token] = []
        square = round_depth = angle = curly = 0
        while self.peek():
            token = self.pop()
            if token.value == "[":
                square += 1
            elif token.value == "]":
                square = max(0, square - 1)
            elif token.value == "(":
                round_depth += 1
            elif token.value == ")":
                round_depth = max(0, round_depth - 1)
            elif token.value == "<":
                angle += 1
            elif token.value == ">":
                angle = max(0, angle - 1)
            elif token.value == "{":
                curly += 1
            elif token.value == "}":
                if curly == 0:
                    self.i -= 1
                    break
                curly -= 1
            result.append(token)
            if token.value == ";" and square == round_depth == angle == curly == 0:
                break
        return result

    def parse_field(
        self, statement: Sequence[Token], message: str, oneof: str | None
    ) -> dict[str, Any] | None:
        if not statement or statement[-1].value != ";":
            return None
        values = [token.value for token in statement[:-1]]
        if "=" not in values:
            return None
        equals = values.index("=")
        if equals < 2:
            return None
        name = values[equals - 1]
        try:
            number = int(values[equals + 1], 0)
        except (ValueError, IndexError):
            return None
        before = values[: equals - 1]
        label = (
            before.pop(0)
            if before and before[0] in {"optional", "required", "repeated"}
            else None
        )
        if not before:
            return None
        return {
            "class": "proto_field",
            "symbol": f"{message}.{name}",
            "field_type": join_type(before),
            "number": number,
            "label": label,
            "oneof": oneof,
            "start": statement[0].line,
            "end": statement[-1].line,
        }

    def skip_declaration(self) -> None:
        if not self.peek():
            return
        depth = 0
        while self.peek():
            token = self.pop()
            if token.value == "{":
                depth += 1
            elif token.value == "}":
                if depth == 0:
                    self.i -= 1
                    return
                depth -= 1
                if depth == 0:
                    self.accept(";")
                    return
            elif token.value == ";" and depth == 0:
                return

    def record_manual(
        self, item_class: str, symbol: str, statement: Sequence[Token]
    ) -> None:
        self.manual.append(
            {
                "class": item_class,
                "symbol": symbol,
                "start": statement[0].line,
                "end": statement[-1].line,
                "tokens": [token.value for token in statement],
            }
        )


def join_type(parts: Iterable[str]) -> str:
    output = ""
    for part in parts:
        if part in {".", ">", ","}:
            output += part
        elif part == "<":
            output += part
        elif output and not output.endswith((".", "<", ",")):
            output += " " + part
        else:
            output += part
    return output


def extract_http_bindings(tokens: Sequence[Token]) -> list[dict[str, Any]]:
    verbs = {"get", "put", "post", "delete", "patch"}
    body: str | None = None
    for index, token in enumerate(tokens[:-2]):
        if (
            token.value == "body"
            and tokens[index + 1].value in {":", "="}
            and tokens[index + 2].kind == "string"
        ):
            body = unquote(tokens[index + 2].value)
    results: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for index, token in enumerate(tokens[:-2]):
        if (
            token.value not in verbs
            or tokens[index + 1].value not in {":", "="}
            or tokens[index + 2].kind != "string"
        ):
            continue
        value = {
            "method": token.value.upper(),
            "path": unquote(tokens[index + 2].value),
            "body": body,
            "start": token.line,
            "end": tokens[index + 2].line,
        }
        results[(value["method"], value["path"], body)] = value
    return [results[key] for key in sorted(results)]


def schema_identity(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    if schema.get("type") == "array":
        return f"array<{schema_identity(schema.get('items')) or 'unknown'}>"
    return schema.get("type") if isinstance(schema.get("type"), str) else None


def parse_openapi(
    document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        return [], [{"class": "invalid_paths", "symbol": "paths"}]
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            manual.append({"class": "invalid_path_item", "symbol": path})
            continue
        for method in sorted({"get", "put", "post", "delete", "patch"} & set(path_item)):
            operation = path_item[method]
            if not isinstance(operation, dict):
                manual.append(
                    {"class": "invalid_operation", "symbol": f"{method}:{path}"}
                )
                continue
            operation_id = str(
                operation.get("operationId") or f"{method.upper()} {path}"
            )
            items.append(
                {
                    "class": "openapi_operation",
                    "symbol": operation_id,
                    "method": method.upper(),
                    "path": path,
                    "request": openapi_request(operation),
                    "responses": openapi_responses(operation),
                    "start": None,
                    "end": None,
                }
            )
    definitions = document.get("definitions", {})
    if not isinstance(definitions, dict):
        manual.append({"class": "invalid_definitions", "symbol": "definitions"})
        definitions = {}
    for name in sorted(definitions):
        schema = definitions[name]
        if not isinstance(schema, dict):
            manual.append({"class": "invalid_schema", "symbol": name})
            continue
        required = {str(value) for value in schema.get("required", [])}
        items.append(
            {
                "class": "openapi_schema",
                "symbol": name,
                "schema_type": schema.get("type"),
                "required": sorted(required),
                "enum": [str(value) for value in schema.get("enum", [])],
                "start": None,
                "end": None,
            }
        )
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for property_name in sorted(properties):
                property_schema = properties[property_name]
                if not isinstance(property_schema, dict):
                    manual.append(
                        {
                            "class": "invalid_property",
                            "symbol": f"{name}.{property_name}",
                        }
                    )
                    continue
                items.append(
                    {
                        "class": "openapi_property",
                        "symbol": f"{name}.{property_name}",
                        "property_type": property_schema.get("type"),
                        "format": property_schema.get("format"),
                        "ref": schema_identity(property_schema),
                        "required": property_name in required,
                        "start": None,
                        "end": None,
                    }
                )
    return items, manual


def openapi_request(operation: dict[str, Any]) -> str | None:
    for parameter in operation.get("parameters", []):
        if isinstance(parameter, dict) and parameter.get("in") == "body":
            return schema_identity(parameter.get("schema"))
    return None


def openapi_responses(operation: dict[str, Any]) -> list[str]:
    result: list[str] = []
    responses = operation.get("responses", {})
    if isinstance(responses, dict):
        for status in sorted(responses):
            response = responses[status]
            if isinstance(response, dict):
                identity = schema_identity(response.get("schema"))
                if identity:
                    result.append(f"{status}:{identity}")
    return result


def source_for(key: str, source_lock: list[dict[str, Any]]) -> dict[str, Any]:
    return next(item for item in source_lock if item["key"] == key)


def make_leaf(
    layer: str, item: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    contract = {
        key: value for key, value in item.items() if key not in {"start", "end"}
    }
    symbol = str(item["symbol"])
    identifier = leaf_id(layer, str(item["class"]), symbol)
    return {
        "id": identifier,
        "layer": layer,
        "class": item["class"],
        "symbol": symbol,
        "source": {
            "repository": source["repository"],
            "commit": source["commit"],
            "path": source["path"],
            "blob": source["blob"],
            "start_line": item.get("start"),
            "end_line": item.get("end"),
        },
        "signature_hash": sha256(canonical(contract)),
        "compatibility_profile": "C1",
        "stability_tier": "wire-contract",
        "classification": "unclassified",
        "mandatory": None,
        "owner_role": "protocol",
        "workstream": "W2",
        "task_ids": ["TG-W0-002"],
        "test_ids": [f"TG-DIFF-{identifier}"],
        "status": "planned",
        "evidence_refs": [],
        "waiver": None,
        "contract": contract,
    }


def normalize_operation_id(value: str) -> str:
    return re.split(r"[._/]", value)[-1]


def reconciliation(api_leaves: Sequence[dict[str, Any]]) -> dict[str, Any]:
    rpc = {
        leaf["contract"]["symbol"].rsplit(".", 1)[-1]
        for leaf in api_leaves
        if leaf["class"] == "grpc_method"
    }
    openapi = {
        normalize_operation_id(leaf["symbol"])
        for leaf in api_leaves
        if leaf["class"] == "openapi_operation"
    }
    proto_routes = {
        (leaf["contract"]["method"], leaf["contract"]["path"])
        for leaf in api_leaves
        if leaf["class"] == "proto_http_binding"
    }
    swagger_routes = {
        (leaf["contract"]["method"], leaf["contract"]["path"])
        for leaf in api_leaves
        if leaf["class"] == "openapi_operation"
    }
    return {
        "schema": "trillionnium.api-rtapi-reconciliation.v1",
        "status": "candidate-unclassified",
        "grpc_method_count": len(rpc),
        "openapi_operation_count": len(openapi),
        "unmatched_grpc_methods": sorted(rpc - openapi),
        "unmatched_openapi_operations": sorted(openapi - rpc),
        "proto_http_binding_count": len(proto_routes),
        "swagger_route_count": len(swagger_routes),
        "unmatched_proto_routes": sorted(
            f"{method} {path}" for method, path in proto_routes - swagger_routes
        ),
        "unmatched_swagger_routes": sorted(
            f"{method} {path}" for method, path in swagger_routes - proto_routes
        ),
        "compatibility_credit": False,
        "sg1_eligible": False,
    }


def build_manifest(
    denominator: str,
    layer: str,
    leaves: list[dict[str, Any]],
    manual: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    leaves.sort(key=lambda leaf: leaf["id"])
    if len({leaf["id"] for leaf in leaves}) != len(leaves):
        raise ContractError(f"duplicate stable IDs in {denominator}")
    manifest = {
        "schema": "trillionnium.parity-denominator-candidate.v1",
        "project_id": "trillionnium-game",
        "generator": {"name": GENERATOR, "version": VERSION},
        "denominator": denominator,
        "layer": layer,
        "status": "candidate-unclassified",
        "leaf_count": len(leaves),
        "unclassified_count": len(leaves),
        "manual_contract_count": len(manual),
        "sg1_eligible": False,
        "compatibility_credit": False,
        "source_lock": sources,
        "leaves": leaves,
        "manual_contracts": sorted(manual, key=canonical),
    }
    manifest["content_sha256"] = sha256(canonical(manifest))
    return manifest


def generate(source_root: Path, output_root: Path) -> dict[str, Any]:
    loaded, source_lock = load_sources(source_root)
    api_items: list[dict[str, Any]] = []
    api_manual: list[dict[str, Any]] = []
    rt_items: list[dict[str, Any]] = []
    rt_manual: list[dict[str, Any]] = []

    for key in ("nakama-apigrpc-proto", "common-api-proto"):
        items, manual = ProtoParser(loaded[key].decode("utf-8")).parse()
        source = source_for(key, source_lock)
        api_items.extend(make_leaf("D1", item, source) for item in items)
        api_manual.extend({**entry, "source_key": key} for entry in manual)
    swagger = json.loads(loaded["nakama-apigrpc-swagger"].decode("utf-8"))
    items, manual = parse_openapi(swagger)
    source = source_for("nakama-apigrpc-swagger", source_lock)
    api_items.extend(make_leaf("D1", item, source) for item in items)
    api_manual.extend(
        {**entry, "source_key": "nakama-apigrpc-swagger"} for entry in manual
    )

    items, manual = ProtoParser(
        loaded["common-rtapi-proto"].decode("utf-8")
    ).parse()
    source = source_for("common-rtapi-proto", source_lock)
    for item in items:
        if (
            item["class"] == "proto_field"
            and item.get("oneof")
            and ".Envelope." in item["symbol"]
        ):
            item = {**item, "class": "realtime_envelope_member"}
        rt_items.append(make_leaf("D2", item, source))
    rt_manual.extend(
        {**entry, "source_key": "common-rtapi-proto"} for entry in manual
    )

    api_sources = [
        source_for(key, source_lock)
        for key in (
            "nakama-apigrpc-proto",
            "nakama-apigrpc-swagger",
            "common-api-proto",
        )
    ]
    rt_sources = [source_for("common-rtapi-proto", source_lock)]
    api_manifest = build_manifest(
        "DEN-API", "D1", api_items, api_manual, api_sources
    )
    rt_manifest = build_manifest(
        "DEN-RTAPI", "D2", rt_items, rt_manual, rt_sources
    )
    reconcile = reconciliation(api_manifest["leaves"])
    snapshot = {
        "schema": "trillionnium.upstream-source-snapshot.v1",
        "generator": {"name": GENERATOR, "version": VERSION},
        "sources": source_lock,
        "content_sha256": sha256(canonical(source_lock)),
    }

    output_root.mkdir(parents=True, exist_ok=True)
    outputs = {
        "api-denominator.candidate.json": api_manifest,
        "rtapi-denominator.candidate.json": rt_manifest,
        "api-rtapi-reconciliation.candidate.json": reconcile,
        "source-snapshot.lock.json": snapshot,
    }
    for name, value in outputs.items():
        write_json(output_root / name, value)
    sums = []
    for name in sorted(outputs):
        sums.append(
            f"{hashlib.sha256((output_root / name).read_bytes()).hexdigest()}  {name}"
        )
    (output_root / "SHA256SUMS").write_text(
        "\n".join(sums) + "\n", encoding="utf-8"
    )
    return {
        "api_leaf_count": api_manifest["leaf_count"],
        "rtapi_leaf_count": rt_manifest["leaf_count"],
        "api_manual_contract_count": api_manifest["manual_contract_count"],
        "rtapi_manual_contract_count": rt_manifest["manual_contract_count"],
        "sg1_eligible": False,
        "compatibility_credit": False,
    }


def require_sg1(output_root: Path) -> None:
    failures: list[str] = []
    for name in (
        "api-denominator.candidate.json",
        "rtapi-denominator.candidate.json",
    ):
        manifest = json.loads((output_root / name).read_text(encoding="utf-8"))
        if manifest.get("status") != "reviewed-locked":
            failures.append(f"{name}: not reviewed-locked")
        if manifest.get("unclassified_count") != 0:
            failures.append(f"{name}: unclassified leaves remain")
        if manifest.get("manual_contract_count") != 0:
            failures.append(f"{name}: manual contracts remain")
        if manifest.get("sg1_eligible") is not True:
            failures.append(f"{name}: sg1_eligible is false")
    if failures:
        raise ContractError("SG1 remains open: " + "; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--fetch", action="store_true", help="fetch exact sources before offline generation"
    )
    parser.add_argument(
        "--require-sg1",
        action="store_true",
        help="fail unless reviewed locked manifests qualify",
    )
    args = parser.parse_args()
    try:
        if args.fetch:
            fetch_sources(args.source_dir)
        summary = generate(args.source_dir, args.output_dir)
        if args.require_sg1:
            require_sg1(args.output_dir)
        print(json.dumps(summary, sort_keys=True))
    except (
        ContractError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        print(f"denominator generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
