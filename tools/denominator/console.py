# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .common import DenominatorError, leaf, license_class, manifest, source_ref

GENERATOR = "trillionniumgame-console-denominator"


def _strip_comments_preserve_lines(text: str) -> str:
    out = list(text)
    i = 0
    in_string: str | None = None
    escaped = False
    while i < len(out):
        ch = out[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in {'"', "'"}:
            in_string = ch
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i + 2)
            j = len(out) if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if text.startswith("/*", i):
            depth = 1
            j = i + 2
            while j < len(out) and depth:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                raise DenominatorError("unterminated block comment")
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _matching_brace(text: str, opening: int) -> int:
    depth = 0
    in_string: str | None = None
    escaped = False
    for i in range(opening, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in {'"', "'"}:
            in_string = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise DenominatorError("unmatched brace")


def _blocks(text: str, keyword: str) -> Iterable[tuple[str, str, int, int]]:
    pattern = re.compile(rf"\b{re.escape(keyword)}\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{{")
    for match in pattern.finditer(text):
        opening = text.find("{", match.start())
        closing = _matching_brace(text, opening)
        start_line = text.count("\n", 0, match.start()) + 1
        end_line = text.count("\n", 0, closing) + 1
        yield match.group(1), text[opening + 1 : closing], start_line, end_line


def parse_proto(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clean = _strip_comments_preserve_lines(text)
    package_match = re.search(r"\bpackage\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;", clean)
    package = package_match.group(1) if package_match else ""
    items: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []

    for name, body, start, end in _blocks(clean, "service"):
        service = f"{package}.{name}" if package else name
        items.append({"class": "console_grpc_service", "symbol": service, "start": start, "end": end})
        rpc_pattern = re.compile(
            r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(stream\s+)?([A-Za-z0-9_.]+)\s*\)\s*"
            r"returns\s*\(\s*(stream\s+)?([A-Za-z0-9_.]+)\s*\)",
            re.MULTILINE,
        )
        for rpc in rpc_pattern.finditer(body):
            line = start + body.count("\n", 0, rpc.start())
            items.append(
                {
                    "class": "console_grpc_method",
                    "symbol": f"{service}.{rpc.group(1)}",
                    "input": rpc.group(3),
                    "output": rpc.group(5),
                    "client_streaming": bool(rpc.group(2)),
                    "server_streaming": bool(rpc.group(4)),
                    "start": line,
                    "end": line + rpc.group(0).count("\n"),
                }
            )

    field_pattern = re.compile(
        r"(?m)^\s*(?:(optional|required|repeated)\s+)?([A-Za-z0-9_.<>]+)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([0-9]+)\b"
    )
    for name, body, start, end in _blocks(clean, "message"):
        symbol = f"{package}.{name}" if package else name
        items.append({"class": "console_proto_message", "symbol": symbol, "start": start, "end": end})
        for field in field_pattern.finditer(body):
            line = start + body.count("\n", 0, field.start())
            items.append(
                {
                    "class": "console_proto_field",
                    "symbol": f"{symbol}.{field.group(3)}",
                    "label": field.group(1),
                    "field_type": field.group(2),
                    "number": int(field.group(4)),
                    "start": line,
                    "end": line,
                }
            )
        if re.search(r"\boneof\b", body):
            manual.append({"class": "proto_oneof_review", "symbol": symbol, "reason": "oneof membership requires dedicated review", "start_line": start, "end_line": end})

    enum_value = re.compile(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?[0-9]+)\b")
    for name, body, start, end in _blocks(clean, "enum"):
        symbol = f"{package}.{name}" if package else name
        items.append({"class": "console_proto_enum", "symbol": symbol, "start": start, "end": end})
        for value in enum_value.finditer(body):
            line = start + body.count("\n", 0, value.start())
            items.append({"class": "console_proto_enum_value", "symbol": f"{symbol}.{value.group(1)}", "number": int(value.group(2)), "start": line, "end": line})

    if not any(item["class"] == "console_grpc_service" for item in items):
        manual.append({"class": "missing_console_service", "symbol": package or "console.proto"})
    return items, manual


def _schema_identity(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    if schema.get("type") == "array":
        return f"array<{_schema_identity(schema.get('items')) or 'unknown'}>"
    return schema.get("type") if isinstance(schema.get("type"), str) else None


def parse_swagger(document: dict[str, Any], namespace: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return [], [{"class": "invalid_swagger_paths", "symbol": namespace}]
    methods = {"get", "put", "post", "delete", "patch", "options", "head"}
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            manual.append({"class": "invalid_swagger_path", "symbol": path})
            continue
        for method in sorted(methods & set(path_item)):
            operation = path_item[method]
            if not isinstance(operation, dict):
                manual.append({"class": "invalid_swagger_operation", "symbol": f"{method.upper()} {path}"})
                continue
            operation_id = str(operation.get("operationId") or f"{method.upper()} {path}")
            request = None
            parameters: list[str] = []
            for parameter in operation.get("parameters", []):
                if not isinstance(parameter, dict):
                    continue
                parameters.append(f"{parameter.get('in')}:{parameter.get('name')}:{bool(parameter.get('required'))}")
                if parameter.get("in") == "body":
                    request = _schema_identity(parameter.get("schema"))
            responses: list[str] = []
            for status, response in sorted((operation.get("responses") or {}).items()):
                if isinstance(response, dict):
                    identity = _schema_identity(response.get("schema"))
                    if identity:
                        responses.append(f"{status}:{identity}")
            items.append({"class": "console_http_operation", "symbol": operation_id, "method": method.upper(), "path": path, "request": request, "parameters": sorted(parameters), "responses": responses, "start": None, "end": None, "swagger_namespace": namespace})

    definitions = document.get("definitions", {})
    if not isinstance(definitions, dict):
        manual.append({"class": "invalid_swagger_definitions", "symbol": namespace})
        definitions = {}
    for name in sorted(definitions):
        schema = definitions[name]
        if not isinstance(schema, dict):
            manual.append({"class": "invalid_swagger_schema", "symbol": name})
            continue
        required = {str(item) for item in schema.get("required", [])}
        items.append({"class": "console_swagger_schema", "symbol": name, "schema_type": schema.get("type"), "required": sorted(required), "enum": [str(item) for item in schema.get("enum", [])], "start": None, "end": None, "swagger_namespace": namespace})
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for property_name in sorted(properties):
                prop = properties[property_name]
                if not isinstance(prop, dict):
                    manual.append({"class": "invalid_swagger_property", "symbol": f"{name}.{property_name}"})
                    continue
                items.append({"class": "console_swagger_property", "symbol": f"{name}.{property_name}", "property_type": prop.get("type"), "format": prop.get("format"), "ref": _schema_identity(prop), "required": property_name in required, "start": None, "end": None, "swagger_namespace": namespace})
    return items, manual


def extract_console(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    proto_path = "console/console.proto"
    proto_data = (root / proto_path).read_bytes()
    proto_items, manual = parse_proto(proto_data.decode("utf-8"))
    leaves: list[dict[str, Any]] = []
    for item in proto_items:
        source = source_ref(root, proto_path, item.pop("start"), item.pop("end"))
        leaves.append(leaf("D3", item.pop("class"), item.pop("symbol"), source, item, owner="console-admin", workstream="W13", task="TG-W0-002"))

    for relative, namespace in (("console/console.swagger.json", "console"), ("console/api.swagger.json", "client-api-explorer")):
        data = (root / relative).read_bytes()
        document = json.loads(data.decode("utf-8"))
        items, extra = parse_swagger(document, namespace)
        manual.extend({**entry, "source_path": relative} for entry in extra)
        for item in items:
            source = source_ref(root, relative)
            leaves.append(leaf("D3", item.pop("class"), item.pop("symbol"), source, item, owner="console-admin", workstream="W13", task="TG-W0-002"))

    acl_path = "console/acl/acl.go"
    acl_data = (root / acl_path).read_bytes()
    acl_license = license_class(acl_data)
    if acl_license != "apache-2.0":
        ref = source_ref(root, acl_path)
        manual.append({"class": "restricted_console_acl_source", "symbol": acl_path, "source": ref.to_dict(), "license_class": acl_license, "reason": "Do not reproduce restricted ACL implementation; derive supported behavior from public contracts and black-box tests after legal review."})
    else:
        manual.append({"class": "console_acl_behavior_review", "symbol": acl_path, "reason": "ACL route/resource mapping requires explicit behavioral review."})

    ui_root = root / "console/ui/dist"
    if ui_root.is_dir():
        for path in sorted(p for p in ui_root.rglob("*") if p.is_file() and not p.name.endswith(".gz")):
            relative = path.relative_to(root).as_posix()
            ref = source_ref(root, relative)
            contract = {"asset_kind": path.suffix.lower() or "none", "size": path.stat().st_size, "semantic_extraction": False}
            leaves.append(leaf("D3", "console_ui_asset", relative, ref, contract, owner="console-admin", workstream="W13", task="TG-W0-002"))
        manual.append({"class": "console_ui_workflow_blackbox_required", "symbol": "console/ui/dist", "reason": "Minified UI and bundled non-Nakama product assets are inventoried only; workflows, accessibility and RBAC must be captured by black-box tests and licensing review."})
    else:
        manual.append({"class": "missing_console_ui", "symbol": "console/ui/dist"})

    result = manifest("DEN-CONSOLE", "D3", leaves, manual, generator=GENERATOR)
    proto_methods = {item["symbol"].rsplit(".", 1)[-1] for item in result["leaves"] if item["class"] == "console_grpc_method"}
    swagger_methods = {re.split(r"[._/]", item["symbol"])[-1] for item in result["leaves"] if item["class"] == "console_http_operation" and item["contract"].get("swagger_namespace") == "console"}
    reconciliation = {
        "schema": "trillionnium.console-reconciliation.v1",
        "status": "candidate-unclassified",
        "proto_method_count": len(proto_methods),
        "swagger_operation_count": len(swagger_methods),
        "unmatched_proto_methods": sorted(proto_methods - swagger_methods),
        "unmatched_swagger_operations": sorted(swagger_methods - proto_methods),
        "restricted_acl_source_present": acl_license != "apache-2.0",
        "sg1_eligible": False,
        "compatibility_credit": False,
    }
    return result, reconciliation
