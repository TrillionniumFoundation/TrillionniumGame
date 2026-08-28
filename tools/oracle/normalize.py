# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

FORBIDDEN_TOKEN_KEYS = {
    "token", "accesstoken", "refreshtoken", "sessiontoken", "authorization", "bearer"
}
JSON_PATH_RE = re.compile(r"^\$\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def load_registry(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("normalizer registry must be an object")
    if value.get("schema") != "trillionnium.oracle-normalizer-registry.v1":
        raise ValueError("unsupported normalizer registry schema")
    if value.get("project_id") != "trillionnium-game":
        raise ValueError("normalizer registry project mismatch")
    allowed = value.get("allowed")
    if not isinstance(allowed, list):
        raise ValueError("normalizer allowed list is required")
    forbidden = [_normalized_key(str(item)) for item in value.get("forbidden_path_fragments", [])]
    seen: set[tuple[str, str]] = set()
    for index, rule in enumerate(allowed):
        if not isinstance(rule, dict):
            raise ValueError(f"normalizer rule {index} must be an object")
        surface = rule.get("surface")
        path_value = rule.get("path")
        reason = rule.get("reason")
        if not isinstance(surface, str) or not surface or not isinstance(path_value, str) or not reason:
            raise ValueError(f"normalizer rule {index} is incomplete")
        if not JSON_PATH_RE.fullmatch(path_value):
            raise ValueError(f"normalizer rule {index} uses unsupported JSON path {path_value!r}")
        normalized_path = _normalized_key(path_value)
        if any(fragment and fragment in normalized_path for fragment in forbidden):
            raise ValueError(f"normalizer rule {index} touches forbidden path {path_value!r}")
        key = (surface, path_value)
        if key in seen:
            raise ValueError(f"duplicate normalizer rule for {surface} {path_value}")
        seen.add(key)
    policy = value.get("policy") or {}
    for field in (
        "raw_access_token_may_be_stored",
        "raw_refresh_token_may_be_stored",
        "identity_divergence_may_be_normalized",
        "authorization_divergence_may_be_normalized",
        "error_code_divergence_may_be_normalized",
        "durable_effect_divergence_may_be_normalized",
    ):
        if policy.get(field) is not False:
            raise ValueError(f"normalizer policy {field} must be false")
    return value


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise ValueError("token is not a compact JWT")
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise ValueError("JWT payload is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("JWT payload must be an object")
    return value


def normalize_jwt(claims: dict[str, Any], surface: str, registry: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(claims)
    allowed = {
        rule["path"].removeprefix("$.")
        for rule in registry["allowed"]
        if rule["surface"] == surface
    }
    for field in allowed:
        if "." in field:
            _delete_path(result, f"$.{field}")
        else:
            result.pop(field, None)
    return result


def _delete_path(value: Any, path: str) -> None:
    segments = path.removeprefix("$.").split(".")
    current = value
    for segment in segments[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return
        current = current[segment]
    if isinstance(current, dict):
        current.pop(segments[-1], None)


def normalize_json(value: Any, surface: str, registry: dict[str, Any]) -> Any:
    result = deepcopy(value)
    for rule in registry["allowed"]:
        if rule["surface"] == surface:
            _delete_path(result, rule["path"])
    return result


def assert_no_raw_tokens(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _normalized_key(key) in FORBIDDEN_TOKEN_KEYS:
                raise ValueError(f"raw token field is forbidden at {path}.{key}")
            assert_no_raw_tokens(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_raw_tokens(child, f"{path}[{index}]")
    elif isinstance(value, str):
        parts = value.split(".")
        if len(parts) == 3 and all(parts):
            try:
                decoded = []
                for part in parts[:2]:
                    padded = part + "=" * (-len(part) % 4)
                    decoded.append(json.loads(base64.urlsafe_b64decode(padded.encode("ascii"))))
            except Exception:
                pass
            else:
                if all(isinstance(item, dict) for item in decoded):
                    raise ValueError(f"compact JWT-shaped value is forbidden at {path}")
