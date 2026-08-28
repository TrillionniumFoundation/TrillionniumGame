"""Nakama-owned context and World transition v1 wire validation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .canonical import (
    MAX_I64,
    CanonicalJsonError,
    canonical_dumps,
    loads_canonical,
)

CONTRACT_VERSION = "trnm_world_transition_v1"
REQUEST_HASH_DOMAIN = "trnm.world.transition.request.v1"
TRANSITION_HASH_DOMAIN = "trnm.world.transition.accepted.v1"
OUTCOME_HASH_DOMAIN = "trnm.world.outcome.v1"
NAKAMA_CONTEXT_DOMAIN = "trnm.nakama.world.transition.context.v1"
TRANSITION_ID_DOMAIN = "trnm.nakama.world.transition.id.v1"
COMMAND_ID_DOMAIN = "trnm.nakama.world.command.id.v1"

MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_COMMAND_BYTES = 128 * 1024
MAX_REPLAY_BYTES = 2 * 1024 * 1024
MAX_OUTCOME_BYTES = 512 * 1024

IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/+@-]{1,160}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

STABLE_ERROR_CODES = frozenset(
    {
        "invalid_contract_version",
        "invalid_request",
        "unknown_ruleset_revision",
        "unknown_content_revision",
        "payload_hash_mismatch",
        "invalid_canonical_payload",
        "forbidden_authority_surface",
        "resource_budget_exceeded",
        "invalid_command",
        "domain_rejected",
        "nondeterministic_output",
        "internal_unavailable",
    }
)

FORBIDDEN_WORLD_AUTHORITY_KEYS = frozenset(
    {
        "nakama_session_token",
        "nakama_private_key",
        "match_authority_private_key",
        "canonical_archive_root",
        "chain_finality",
        "chain_app_hash",
        "match_completed_v1",
        "participant_admission_receipt",
        "global_event_cursor",
        "participant_roster",
        "participant_roles",
        "completion_signature",
        "authority_key_id",
        "wallet_balance",
    }
)

REQUEST_FIELDS = frozenset(
    {
        "command",
        "content_revision",
        "contract_version",
        "expected_tick",
        "previous_state",
        "ruleset_revision",
        "transition_id",
    }
)
COMMAND_FIELDS = frozenset({"command_id", "payload"})
PAYLOAD_FIELDS = frozenset({"canonical_json", "schema_id", "sha256"})
ACCEPTED_FIELDS = frozenset(
    {
        "content_revision",
        "contract_version",
        "next_state",
        "next_tick",
        "outcome_material",
        "previous_state_hash",
        "replay_material",
        "request_hash",
        "ruleset_revision",
        "transition_id",
        "world_outcome_hash",
        "world_transition_hash",
    }
)
REJECTED_FIELDS = frozenset(
    {
        "code",
        "contract_version",
        "detail",
        "request_hash",
        "retryable",
        "transition_id",
    }
)


class TransitionContractError(ValueError):
    """A fail-closed World transition boundary violation."""


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def domain_hash(domain: str, canonical_json: str) -> str:
    if not domain or not domain.isascii() or "\n" in domain:
        raise TransitionContractError("hash domain must be single-line ASCII")
    return sha256_hex(domain.encode("ascii") + b"\n" + canonical_json.encode("utf-8"))


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise TransitionContractError(f"{label} must be a portable identifier")
    return value


def _require_hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise TransitionContractError(f"{label} must be lowercase 64-hex")
    return value


def _require_i64_nonnegative(value: Any, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= MAX_I64
    ):
        raise TransitionContractError(f"{label} must be a non-negative signed i64")
    return value


def _require_exact_fields(
    value: Any, expected: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TransitionContractError(f"{label} must be an object")
    actual = frozenset(value)
    missing = expected - actual
    extra = actual - expected
    if missing:
        raise TransitionContractError(f"{label} missing fields: {sorted(missing)}")
    if extra:
        authority = extra & FORBIDDEN_WORLD_AUTHORITY_KEYS
        if authority:
            raise TransitionContractError(
                f"{label} contains forbidden authority fields: {sorted(authority)}"
            )
        raise TransitionContractError(f"{label} has unknown fields: {sorted(extra)}")
    return value


def _reject_authority_keys(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_WORLD_AUTHORITY_KEYS:
                raise TransitionContractError(
                    f"{label} contains forbidden authority key: {key}"
                )
            _reject_authority_keys(item, label)
    elif isinstance(value, list):
        for item in value:
            _reject_authority_keys(item, label)


@dataclass(frozen=True)
class CanonicalPayload:
    schema_id: str
    canonical_json: Any
    sha256: str
    canonical_bytes: bytes

    @classmethod
    def from_value(
        cls,
        value: Any,
        *,
        schema_id: str,
        maximum_bytes: int,
        label: str,
    ) -> "CanonicalPayload":
        _require_identifier(schema_id, f"{label}.schema_id")
        _reject_authority_keys(value, label)
        canonical = canonical_dumps(value, root_container=True)
        encoded = canonical.encode("utf-8")
        if len(encoded) > maximum_bytes:
            raise TransitionContractError(
                f"{label} exceeds {maximum_bytes} canonical bytes"
            )
        return cls(
            schema_id=schema_id,
            canonical_json=value,
            sha256=sha256_hex(encoded),
            canonical_bytes=encoded,
        )

    @classmethod
    def from_wire(
        cls,
        value: Any,
        *,
        maximum_bytes: int,
        label: str,
    ) -> "CanonicalPayload":
        payload = _require_exact_fields(value, PAYLOAD_FIELDS, label)
        schema_id = _require_identifier(payload["schema_id"], f"{label}.schema_id")
        canonical_json = payload["canonical_json"]
        _reject_authority_keys(canonical_json, label)
        canonical = canonical_dumps(canonical_json, root_container=True)
        encoded = canonical.encode("utf-8")
        if len(encoded) > maximum_bytes:
            raise TransitionContractError(
                f"{label} exceeds {maximum_bytes} canonical bytes"
            )
        supplied = _require_hex64(payload["sha256"], f"{label}.sha256")
        actual = sha256_hex(encoded)
        if actual != supplied:
            raise TransitionContractError(f"{label} payload hash mismatch")
        return cls(
            schema_id=schema_id,
            canonical_json=canonical_json,
            sha256=supplied,
            canonical_bytes=encoded,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "canonical_json": self.canonical_json,
            "schema_id": self.schema_id,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class NakamaAuthorityContext:
    match_id: str
    authorization_id: str
    participant_roster_hash: str
    match_version: int
    global_event_sequence: int
    command_idempotency_key: str
    ruleset_revision: str
    content_revision: str
    expected_tick: int

    def validate(self) -> None:
        _require_identifier(self.match_id, "match_id")
        _require_identifier(self.authorization_id, "authorization_id")
        _require_hex64(self.participant_roster_hash, "participant_roster_hash")
        _require_i64_nonnegative(self.match_version, "match_version")
        _require_i64_nonnegative(
            self.global_event_sequence, "global_event_sequence"
        )
        _require_identifier(
            self.command_idempotency_key, "command_idempotency_key"
        )
        _require_identifier(self.ruleset_revision, "ruleset_revision")
        _require_identifier(self.content_revision, "content_revision")
        _require_i64_nonnegative(self.expected_tick, "expected_tick")

    def canonical_authority_binding(self) -> str:
        self.validate()
        return canonical_dumps(
            {
                "authorization_id": self.authorization_id,
                "command_idempotency_key": self.command_idempotency_key,
                "content_revision": self.content_revision,
                "expected_tick": self.expected_tick,
                "global_event_sequence": self.global_event_sequence,
                "match_id": self.match_id,
                "match_version": self.match_version,
                "participant_roster_hash": self.participant_roster_hash,
                "ruleset_revision": self.ruleset_revision,
            }
        )

    def fingerprint(self) -> str:
        return domain_hash(
            NAKAMA_CONTEXT_DOMAIN, self.canonical_authority_binding()
        )


@dataclass(frozen=True)
class PreparedWorldTransition:
    context: NakamaAuthorityContext
    request: Mapping[str, Any]
    canonical_request: str
    request_hash: str
    transition_id: str
    command_id: str
    previous_state_hash: str

    def request_json(self) -> str:
        return self.canonical_request


@dataclass(frozen=True)
class VerifiedWorldTransition:
    context: NakamaAuthorityContext
    authority_context_fingerprint: str
    request_hash: str
    transition_id: str
    disposition: str
    next_tick: int | None
    previous_state_hash: str | None
    next_state_hash: str | None
    replay_hash: str | None
    world_outcome_hash: str | None
    world_transition_hash: str | None
    error_code: str | None
    retryable: bool | None
    canonical_result: str
    canonical_result_sha256: str

    def to_record(self) -> dict[str, Any]:
        return {
            "authority_context": {
                "authorization_id": self.context.authorization_id,
                "command_idempotency_key": self.context.command_idempotency_key,
                "global_event_sequence": self.context.global_event_sequence,
                "match_id": self.context.match_id,
                "match_version": self.context.match_version,
                "participant_roster_hash": self.context.participant_roster_hash,
            },
            "authority_context_fingerprint": self.authority_context_fingerprint,
            "canonical_result_sha256": self.canonical_result_sha256,
            "disposition": self.disposition,
            "error_code": self.error_code,
            "next_state_hash": self.next_state_hash,
            "next_tick": self.next_tick,
            "previous_state_hash": self.previous_state_hash,
            "replay_hash": self.replay_hash,
            "request_hash": self.request_hash,
            "retryable": self.retryable,
            "transition_id": self.transition_id,
            "world_outcome_hash": self.world_outcome_hash,
            "world_transition_hash": self.world_transition_hash,
        }


def parse_canonical_message(raw: str | bytes) -> dict[str, Any]:
    try:
        value = loads_canonical(raw, root_container=True)
    except CanonicalJsonError as error:
        raise TransitionContractError(str(error)) from error
    if not isinstance(value, dict):
        raise TransitionContractError("transition message root must be an object")
    return value
