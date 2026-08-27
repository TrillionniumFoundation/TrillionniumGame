#!/usr/bin/env python3
"""Independent Nakama-side consumer model for TRNM World runtime v1.

The consumer verifies unsigned deterministic World game-domain material against
an already authoritative Nakama context.  It deliberately does not construct
or sign MatchCompletedV1 and never interprets World batch ordinals as a global
match/event sequence.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

MIN_I64 = -(2**63)
MAX_I64 = 2**63 - 1
MAX_DEPTH = 64
MAX_NODES = 100_000
MAX_BYTES = 16 * 1024 * 1024
HEX = set("0123456789abcdef")
REQUEST_FIELDS = {
    "contract_version",
    "message_type",
    "ruleset",
    "content_digest",
    "initial_state",
    "commands",
}
RESULT_FIELDS = {
    "contract_version",
    "message_type",
    "ruleset",
    "content_digest",
    "initial_state_hash",
    "command_batch_hash",
    "final_state",
    "final_state_hash",
    "outcome",
    "outcome_hash",
    "replay_material",
    "replay_material_hash",
}
RULESET_FIELDS = {"id", "version", "digest"}
COMMAND_FIELDS = {"batch_ordinal", "kind", "payload"}
FORBIDDEN_WORLD_AUTHORITY_FIELDS = {
    "participant_roster",
    "global_sequence",
    "event_root",
    "roster_root",
    "archive_root",
    "completion_signature",
    "authority_key_id",
    "chain_finality",
    "inclusion_proof",
    "wallet_balance",
}
DOMAINS = {
    "initial_state_hash": "trnm.world.runtime.v1.initial_state",
    "command_batch_hash": "trnm.world.runtime.v1.command_batch",
    "final_state_hash": "trnm.world.runtime.v1.final_state",
    "outcome_hash": "trnm.world.runtime.v1.outcome",
    "replay_material_hash": "trnm.world.runtime.v1.replay_material",
}


class ConsumerError(ValueError):
    pass


@dataclass(frozen=True)
class NakamaAuthorityContext:
    match_id: str
    authorization_id: str
    roster_hash: str
    next_global_event_sequence: int
    ruleset_digest: str
    content_digest: str


@dataclass(frozen=True)
class VerifiedWorldExecution:
    match_id: str
    authorization_id: str
    roster_hash: str
    next_global_event_sequence: int
    ruleset_digest: str
    content_digest: str
    initial_state_hash: str
    command_batch_hash: str
    final_state_hash: str
    outcome_hash: str
    replay_material_hash: str


def parse_int(raw: str) -> int:
    value = int(raw)
    if not MIN_I64 <= value <= MAX_I64:
        raise ConsumerError("integer is outside signed 64-bit range")
    return value


def reject_float(raw: str) -> None:
    raise ConsumerError(f"floating-point numbers are forbidden: {raw}")


def strict_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw_seen: set[str] = set()
    for raw_key, value in pairs:
        if raw_key in raw_seen:
            raise ConsumerError(f"duplicate object key: {raw_key}")
        raw_seen.add(raw_key)
        key = unicodedata.normalize("NFC", raw_key)
        if key in result:
            raise ConsumerError(f"normalized object key collision: {key}")
        result[key] = value
    return result


def loads_strict(text: str) -> Any:
    return json.loads(
        text,
        parse_int=parse_int,
        parse_float=reject_float,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ConsumerError(f"non-finite number is forbidden: {value}")
        ),
        object_pairs_hook=strict_object,
    )


def normalize(value: Any, state: list[int], depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise ConsumerError("canonical depth limit exceeded")
    state[0] += 1
    if state[0] > MAX_NODES:
        raise ConsumerError("canonical node limit exceeded")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if not MIN_I64 <= value <= MAX_I64:
            raise ConsumerError("integer is outside signed 64-bit range")
        return value
    if isinstance(value, float):
        raise ConsumerError("floating-point numbers are forbidden")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [normalize(item, state, depth + 1) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise ConsumerError("object key must be a string")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise ConsumerError(f"normalized object key collision: {key}")
            normalized[key] = normalize(item, state, depth + 1)
        return {
            key: normalized[key]
            for key in sorted(normalized, key=lambda candidate: candidate.encode("utf-8"))
        }
    raise ConsumerError(f"unsupported canonical type: {type(value).__name__}")


def canonical(value: Any) -> bytes:
    encoded = json.dumps(
        normalize(value, [0]),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ConsumerError("canonical byte limit exceeded")
    return encoded


def domain_hash(domain: str, value: Any) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\n" + canonical(value)).hexdigest()


def require_hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in HEX for ch in value):
        raise ConsumerError(f"{label} must be lowercase 64-hex")
    return value


def require_exact(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConsumerError(f"{label} must be an object")
    extra = set(value) - expected
    missing = expected - set(value)
    if extra:
        if extra & FORBIDDEN_WORLD_AUTHORITY_FIELDS:
            raise ConsumerError(f"World result contains an authority field: {sorted(extra)}")
        raise ConsumerError(f"{label} has unknown fields: {sorted(extra)}")
    if missing:
        raise ConsumerError(f"{label} is missing fields: {sorted(missing)}")
    return value


def validate_request(value: Any) -> dict[str, Any]:
    request = require_exact(value, REQUEST_FIELDS, "World request")
    if request["contract_version"] != "trnm_world_runtime_v1":
        raise ConsumerError("unsupported World contract version")
    if request["message_type"] != "execute_request":
        raise ConsumerError("World request has wrong message_type")
    require_exact(request["ruleset"], RULESET_FIELDS, "World ruleset")
    require_hex64(request["ruleset"]["digest"], "ruleset.digest")
    require_hex64(request["content_digest"], "content_digest")
    commands = request["commands"]
    if not isinstance(commands, list):
        raise ConsumerError("World commands must be an array")
    for expected_ordinal, command in enumerate(commands):
        command = require_exact(command, COMMAND_FIELDS, f"World command {expected_ordinal}")
        if command["batch_ordinal"] != expected_ordinal:
            raise ConsumerError("World command ordinals must be contiguous from zero")
        canonical(command["payload"])
    canonical(request["initial_state"])
    canonical(commands)
    return request


def validate_context(context: NakamaAuthorityContext) -> None:
    if not context.match_id or not context.authorization_id:
        raise ConsumerError("Nakama authority context identity is missing")
    require_hex64(context.roster_hash, "Nakama roster_hash")
    require_hex64(context.ruleset_digest, "Nakama ruleset_digest")
    require_hex64(context.content_digest, "Nakama content_digest")
    if not 0 <= context.next_global_event_sequence <= MAX_I64:
        raise ConsumerError("Nakama global event sequence is outside range")


def verify_world_execution(
    context: NakamaAuthorityContext,
    request_value: Any,
    result_value: Any,
) -> VerifiedWorldExecution:
    validate_context(context)
    request = validate_request(request_value)
    result = require_exact(result_value, RESULT_FIELDS, "World result")
    if result["contract_version"] != "trnm_world_runtime_v1":
        raise ConsumerError("unsupported World result contract version")
    if result["message_type"] != "execute_result":
        raise ConsumerError("World result has wrong message_type")
    require_exact(result["ruleset"], RULESET_FIELDS, "World result ruleset")

    if request["ruleset"] != result["ruleset"]:
        raise ConsumerError("World result ruleset differs from request")
    if request["content_digest"] != result["content_digest"]:
        raise ConsumerError("World result content digest differs from request")
    if context.ruleset_digest != request["ruleset"]["digest"]:
        raise ConsumerError("World ruleset differs from Nakama selected authority context")
    if context.content_digest != request["content_digest"]:
        raise ConsumerError("World content differs from Nakama selected authority context")

    expected = {
        "initial_state_hash": domain_hash(DOMAINS["initial_state_hash"], request["initial_state"]),
        "command_batch_hash": domain_hash(DOMAINS["command_batch_hash"], request["commands"]),
        "final_state_hash": domain_hash(DOMAINS["final_state_hash"], result["final_state"]),
        "outcome_hash": domain_hash(DOMAINS["outcome_hash"], result["outcome"]),
        "replay_material_hash": domain_hash(
            DOMAINS["replay_material_hash"], result["replay_material"]
        ),
    }
    for field, expected_hash in expected.items():
        require_hex64(result[field], field)
        if result[field] != expected_hash:
            raise ConsumerError(f"World result {field} does not bind its canonical value")

    return VerifiedWorldExecution(
        match_id=context.match_id,
        authorization_id=context.authorization_id,
        roster_hash=context.roster_hash,
        next_global_event_sequence=context.next_global_event_sequence,
        ruleset_digest=context.ruleset_digest,
        content_digest=context.content_digest,
        **expected,
    )


def unsigned_result_for_test(request: dict[str, Any]) -> dict[str, Any]:
    final_state = {"tick": 9, "units": [{"hp": 8, "id": "alpha"}]}
    outcome = {"result": "victory", "score": 10}
    replay = {"frames": [0, 4, 9], "ruleset": request["ruleset"]["digest"]}
    return {
        "contract_version": "trnm_world_runtime_v1",
        "message_type": "execute_result",
        "ruleset": request["ruleset"],
        "content_digest": request["content_digest"],
        "initial_state_hash": domain_hash(DOMAINS["initial_state_hash"], request["initial_state"]),
        "command_batch_hash": domain_hash(DOMAINS["command_batch_hash"], request["commands"]),
        "final_state": final_state,
        "final_state_hash": domain_hash(DOMAINS["final_state_hash"], final_state),
        "outcome": outcome,
        "outcome_hash": domain_hash(DOMAINS["outcome_hash"], outcome),
        "replay_material": replay,
        "replay_material_hash": domain_hash(DOMAINS["replay_material_hash"], replay),
    }


def expect_failure(fragment: str, operation: Any) -> None:
    try:
        operation()
    except (ConsumerError, json.JSONDecodeError) as error:
        if fragment not in str(error):
            raise ConsumerError(f"expected error fragment {fragment!r}, received {error!r}") from error
        return
    raise ConsumerError(f"negative operation unexpectedly succeeded; expected {fragment!r}")


def self_test(root: Path) -> dict[str, Any]:
    vectors = loads_strict((root / "testdata/world-runtime-v1/golden-vectors.json").read_text(encoding="utf-8"))
    if vectors.get("contract_version") != "trnm_world_runtime_golden_vectors_v1":
        raise ConsumerError("unexpected World vector version")
    for vector in vectors["sha256_known_vectors"]:
        actual = hashlib.sha256(vector["bytes_utf8"].encode("utf-8")).hexdigest()
        if actual != vector["sha256"]:
            raise ConsumerError(f"SHA vector failed: {vector['name']}")
    for vector in vectors["canonicalization_vectors"]:
        actual = canonical(vector["value"]).decode("utf-8")
        if actual != vector["expected_canonical"]:
            raise ConsumerError(f"canonical vector failed: {vector['name']}")
        if "expected_sha256" in vector and hashlib.sha256(actual.encode("utf-8")).hexdigest() != vector["expected_sha256"]:
            raise ConsumerError(f"canonical hash vector failed: {vector['name']}")

    request = validate_request(vectors["runtime_request_vector"]["value"])
    context = NakamaAuthorityContext(
        match_id="nakama-match-0001",
        authorization_id="nakama-authorization-0001",
        roster_hash="3" * 64,
        next_global_event_sequence=9_001,
        ruleset_digest=request["ruleset"]["digest"],
        content_digest=request["content_digest"],
    )
    result = unsigned_result_for_test(request)
    verified = verify_world_execution(context, request, result)
    if verified.next_global_event_sequence != 9_001:
        raise ConsumerError("World batch ordinal was incorrectly promoted to global sequence")
    if max(command["batch_ordinal"] for command in request["commands"]) == verified.next_global_event_sequence:
        raise ConsumerError("test context failed to separate local ordinal and global sequence")

    tampered = loads_strict(json.dumps(result, ensure_ascii=False))
    tampered["outcome"]["score"] = 11
    expect_failure(
        "outcome_hash does not bind",
        lambda: verify_world_execution(context, request, tampered),
    )
    authority = loads_strict(json.dumps(result, ensure_ascii=False))
    authority["completion_signature"] = "forbidden"
    expect_failure(
        "authority field",
        lambda: verify_world_execution(context, request, authority),
    )
    ordinal = loads_strict(json.dumps(request, ensure_ascii=False))
    ordinal["commands"][1]["batch_ordinal"] = 2
    expect_failure("contiguous", lambda: verify_world_execution(context, ordinal, result))
    wrong_context = NakamaAuthorityContext(
        match_id=context.match_id,
        authorization_id=context.authorization_id,
        roster_hash=context.roster_hash,
        next_global_event_sequence=context.next_global_event_sequence,
        ruleset_digest="4" * 64,
        content_digest=context.content_digest,
    )
    expect_failure(
        "Nakama selected authority context",
        lambda: verify_world_execution(wrong_context, request, result),
    )

    return {
        "status": "ok",
        "contract_version": "trnm_nakama_world_runtime_consumer_v1",
        "verified_match_id": verified.match_id,
        "nakama_global_sequence": verified.next_global_event_sequence,
        "largest_world_batch_ordinal": max(
            command["batch_ordinal"] for command in request["commands"]
        ),
        "authority_signing_performed": False,
        "world_ordinal_used_as_global_sequence": False,
        "world_authority_fields_accepted": False,
        "outcome_hash": verified.outcome_hash,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        report = self_test(root)
    except (OSError, KeyError, TypeError, ConsumerError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
