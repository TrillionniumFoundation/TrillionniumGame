"""Strict canonical JSON support for the World transition v1 boundary.

This module is deliberately standard-library only. It validates the exact
canonical bytes used by the World contract rather than normalizing arbitrary
JSON into an apparently valid request after the fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

MIN_I64 = -(2**63)
MAX_I64 = 2**63 - 1
MAX_CANONICAL_DEPTH = 128


class CanonicalJsonError(ValueError):
    """Raised when JSON is not valid canonical World transition JSON."""


@dataclass(frozen=True)
class _ObjectPairs:
    pairs: tuple[tuple[str, Any], ...]


def _parse_int(raw: str) -> int:
    if raw == "-0":
        raise CanonicalJsonError("-0 is not a canonical integer")
    value = int(raw, 10)
    if not MIN_I64 <= value <= MAX_I64:
        raise CanonicalJsonError("integer is outside signed 64-bit range")
    return value


def _reject_float(raw: str) -> None:
    raise CanonicalJsonError(f"floating-point numbers are forbidden: {raw}")


def _reject_constant(raw: str) -> None:
    raise CanonicalJsonError(f"non-finite numbers are forbidden: {raw}")


def _pairs_hook(pairs: Iterable[tuple[str, Any]]) -> _ObjectPairs:
    return _ObjectPairs(tuple(pairs))


def _materialize(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_CANONICAL_DEPTH:
        raise CanonicalJsonError(
            f"canonical nesting depth exceeds {MAX_CANONICAL_DEPTH}"
        )
    if isinstance(value, _ObjectPairs):
        result: dict[str, Any] = {}
        previous_key: bytes | None = None
        for key, item in value.pairs:
            if key in result:
                raise CanonicalJsonError(f"duplicate object key: {key}")
            key_bytes = key.encode("utf-8")
            if previous_key is not None and key_bytes <= previous_key:
                raise CanonicalJsonError(
                    "object keys must be strictly ascending by UTF-8 bytes"
                )
            previous_key = key_bytes
            result[key] = _materialize(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_materialize(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if not MIN_I64 <= value <= MAX_I64:
            raise CanonicalJsonError("integer is outside signed 64-bit range")
        return value
    raise CanonicalJsonError(f"unsupported JSON value: {type(value).__name__}")


def _encode_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical_dumps(value: Any, *, root_container: bool = False) -> str:
    """Return exact canonical JSON for an already decoded value."""

    def encode(item: Any, depth: int) -> str:
        if depth > MAX_CANONICAL_DEPTH:
            raise CanonicalJsonError(
                f"canonical nesting depth exceeds {MAX_CANONICAL_DEPTH}"
            )
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int) and not isinstance(item, bool):
            if not MIN_I64 <= item <= MAX_I64:
                raise CanonicalJsonError(
                    "integer is outside signed 64-bit range"
                )
            return str(item)
        if isinstance(item, float):
            raise CanonicalJsonError("floating-point numbers are forbidden")
        if isinstance(item, str):
            return _encode_string(item)
        if isinstance(item, list):
            return "[" + ",".join(
                encode(child, depth + 1) for child in item
            ) + "]"
        if isinstance(item, dict):
            for key in item:
                if not isinstance(key, str):
                    raise CanonicalJsonError("object key must be a string")
            ordered = sorted(item, key=lambda key: key.encode("utf-8"))
            return "{" + ",".join(
                f"{_encode_string(key)}:{encode(item[key], depth + 1)}"
                for key in ordered
            ) + "}"
        raise CanonicalJsonError(
            f"unsupported canonical value: {type(item).__name__}"
        )

    if root_container and not isinstance(value, (dict, list)):
        raise CanonicalJsonError("canonical root must be an object or array")
    return encode(value, 0)


def loads_canonical(
    raw: str | bytes,
    *,
    root_container: bool = False,
    maximum_bytes: int | None = None,
) -> Any:
    """Parse exact canonical JSON and reject any alternate representation."""

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise CanonicalJsonError("input is not valid UTF-8") from error
    elif isinstance(raw, str):
        text = raw
    else:
        raise TypeError("raw JSON must be str or bytes")

    if text.startswith("\ufeff"):
        raise CanonicalJsonError("UTF-8 BOM is forbidden")
    if maximum_bytes is not None and len(text.encode("utf-8")) > maximum_bytes:
        raise CanonicalJsonError(
            f"canonical JSON exceeds {maximum_bytes} bytes"
        )
    try:
        parsed = json.loads(
            text,
            parse_int=_parse_int,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs_hook,
        )
    except (json.JSONDecodeError, CanonicalJsonError) as error:
        if isinstance(error, CanonicalJsonError):
            raise
        raise CanonicalJsonError(str(error)) from error

    value = _materialize(parsed)
    canonical = canonical_dumps(value, root_container=root_container)
    if canonical != text:
        raise CanonicalJsonError(
            "JSON bytes are valid but not the exact canonical representation"
        )
    return value
