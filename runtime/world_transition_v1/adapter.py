"""Nakama-side adapter for the unsigned World transition v1 contract."""

from __future__ import annotations

from typing import Any

from .canonical import MAX_I64, canonical_dumps
from .contracts import (
    ACCEPTED_FIELDS,
    COMMAND_ID_DOMAIN,
    CONTRACT_VERSION,
    MAX_COMMAND_BYTES,
    MAX_OUTCOME_BYTES,
    MAX_REPLAY_BYTES,
    MAX_STATE_BYTES,
    OUTCOME_HASH_DOMAIN,
    REJECTED_FIELDS,
    REQUEST_FIELDS,
    REQUEST_HASH_DOMAIN,
    STABLE_ERROR_CODES,
    TRANSITION_HASH_DOMAIN,
    TRANSITION_ID_DOMAIN,
    CanonicalPayload,
    NakamaAuthorityContext,
    PreparedWorldTransition,
    TransitionContractError,
    VerifiedWorldTransition,
    _reject_authority_keys,
    _require_exact_fields,
    _require_hex64,
    _require_i64_nonnegative,
    domain_hash,
    parse_canonical_message,
    sha256_hex,
)


def _opaque_identifier(prefix: str, domain: str, canonical_binding: str) -> str:
    return f"{prefix}{domain_hash(domain, canonical_binding)[:48]}"


def prepare_world_transition(
    context: NakamaAuthorityContext,
    *,
    previous_state_schema_id: str,
    previous_state: Any,
    command_schema_id: str,
    command: Any,
) -> PreparedWorldTransition:
    """Create one World request from an already-authoritative Nakama context.

    Participant, roster, global-order and idempotency values are used only to
    derive opaque stable correlation IDs. They never cross the World payload
    boundary as authority-bearing fields.
    """

    context.validate()
    binding = context.canonical_authority_binding()
    transition_id = _opaque_identifier("wtx-", TRANSITION_ID_DOMAIN, binding)
    command_id = _opaque_identifier("wcmd-", COMMAND_ID_DOMAIN, binding)
    state_payload = CanonicalPayload.from_value(
        previous_state,
        schema_id=previous_state_schema_id,
        maximum_bytes=MAX_STATE_BYTES,
        label="previous_state",
    )
    command_payload = CanonicalPayload.from_value(
        command,
        schema_id=command_schema_id,
        maximum_bytes=MAX_COMMAND_BYTES,
        label="command.payload",
    )
    request: dict[str, Any] = {
        "command": {
            "command_id": command_id,
            "payload": command_payload.to_wire(),
        },
        "content_revision": context.content_revision,
        "contract_version": CONTRACT_VERSION,
        "expected_tick": context.expected_tick,
        "previous_state": state_payload.to_wire(),
        "ruleset_revision": context.ruleset_revision,
        "transition_id": transition_id,
    }
    if frozenset(request) != REQUEST_FIELDS:
        raise AssertionError("internal request field set drift")
    canonical_request = canonical_dumps(request, root_container=True)
    request_hash = domain_hash(REQUEST_HASH_DOMAIN, canonical_request)
    return PreparedWorldTransition(
        context=context,
        request=request,
        canonical_request=canonical_request,
        request_hash=request_hash,
        transition_id=transition_id,
        command_id=command_id,
        previous_state_hash=state_payload.sha256,
    )


def prepared_from_canonical_request(
    context: NakamaAuthorityContext,
    raw_request: str | bytes,
) -> PreparedWorldTransition:
    """Reconstruct and verify a prepared request for replay/restart recovery."""

    context.validate()
    request = parse_canonical_message(raw_request)
    _require_exact_fields(request, REQUEST_FIELDS, "World request")
    if request["contract_version"] != CONTRACT_VERSION:
        raise TransitionContractError("World request contract version mismatch")
    if request["ruleset_revision"] != context.ruleset_revision:
        raise TransitionContractError("World request ruleset revision mismatch")
    if request["content_revision"] != context.content_revision:
        raise TransitionContractError("World request content revision mismatch")
    if request["expected_tick"] != context.expected_tick:
        raise TransitionContractError("World request expected_tick mismatch")
    binding = context.canonical_authority_binding()
    expected_transition_id = _opaque_identifier(
        "wtx-", TRANSITION_ID_DOMAIN, binding
    )
    expected_command_id = _opaque_identifier(
        "wcmd-", COMMAND_ID_DOMAIN, binding
    )
    if request["transition_id"] != expected_transition_id:
        raise TransitionContractError(
            "World request transition_id is not bound to Nakama context"
        )
    command = _require_exact_fields(
        request["command"],
        frozenset({"command_id", "payload"}),
        "World command",
    )
    if command["command_id"] != expected_command_id:
        raise TransitionContractError(
            "World command_id is not bound to Nakama idempotency context"
        )
    previous_state = CanonicalPayload.from_wire(
        request["previous_state"],
        maximum_bytes=MAX_STATE_BYTES,
        label="previous_state",
    )
    CanonicalPayload.from_wire(
        command["payload"],
        maximum_bytes=MAX_COMMAND_BYTES,
        label="command.payload",
    )
    canonical_request = canonical_dumps(request, root_container=True)
    return PreparedWorldTransition(
        context=context,
        request=request,
        canonical_request=canonical_request,
        request_hash=domain_hash(REQUEST_HASH_DOMAIN, canonical_request),
        transition_id=expected_transition_id,
        command_id=expected_command_id,
        previous_state_hash=previous_state.sha256,
    )


def _outcome_hash(
    ruleset_revision: str,
    content_revision: str,
    outcome: CanonicalPayload,
) -> str:
    binding = canonical_dumps(
        {
            "content_revision": content_revision,
            "outcome_payload_hash": outcome.sha256,
            "outcome_schema_id": outcome.schema_id,
            "ruleset_revision": ruleset_revision,
        }
    )
    return domain_hash(OUTCOME_HASH_DOMAIN, binding)


def _accepted_facts(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key]
        for key in (
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
        )
    }


def verify_world_result(
    prepared: PreparedWorldTransition,
    raw_result: str | bytes,
) -> VerifiedWorldTransition:
    """Verify an unsigned World result without granting it online authority."""

    result = parse_canonical_message(raw_result)
    canonical_result = canonical_dumps(result, root_container=True)
    canonical_result_sha256 = sha256_hex(canonical_result.encode("utf-8"))

    if frozenset(result) == ACCEPTED_FIELDS:
        return _verify_accepted(
            prepared, result, canonical_result, canonical_result_sha256
        )
    if frozenset(result) == REJECTED_FIELDS:
        return _verify_rejected(
            prepared, result, canonical_result, canonical_result_sha256
        )

    actual = frozenset(result)
    authority = actual & {
        "completion_signature",
        "match_completed_v1",
        "canonical_archive_root",
        "global_event_cursor",
        "participant_admission_receipt",
        "chain_finality",
        "wallet_balance",
    }
    if authority:
        raise TransitionContractError(
            f"World result contains forbidden authority fields: {sorted(authority)}"
        )
    raise TransitionContractError(
        "World result does not match accepted or rejected field set"
    )


def _verify_identity(
    prepared: PreparedWorldTransition, result: dict[str, Any]
) -> None:
    if result["contract_version"] != CONTRACT_VERSION:
        raise TransitionContractError("World result contract version mismatch")
    if result["transition_id"] != prepared.transition_id:
        raise TransitionContractError("World result transition_id mismatch")


def _verify_accepted(
    prepared: PreparedWorldTransition,
    result: dict[str, Any],
    canonical_result: str,
    canonical_result_sha256: str,
) -> VerifiedWorldTransition:
    _require_exact_fields(result, ACCEPTED_FIELDS, "accepted World result")
    _verify_identity(prepared, result)
    if result["ruleset_revision"] != prepared.context.ruleset_revision:
        raise TransitionContractError("World result ruleset revision mismatch")
    if result["content_revision"] != prepared.context.content_revision:
        raise TransitionContractError("World result content revision mismatch")
    request_hash = _require_hex64(result["request_hash"], "request_hash")
    if request_hash != prepared.request_hash:
        raise TransitionContractError("World result request_hash mismatch")
    previous_state_hash = _require_hex64(
        result["previous_state_hash"], "previous_state_hash"
    )
    if previous_state_hash != prepared.previous_state_hash:
        raise TransitionContractError(
            "World result previous_state_hash mismatch"
        )
    next_tick = _require_i64_nonnegative(result["next_tick"], "next_tick")
    if next_tick < prepared.context.expected_tick or next_tick > MAX_I64:
        raise TransitionContractError(
            "World result next_tick regresses deterministic state"
        )

    next_state = CanonicalPayload.from_wire(
        result["next_state"],
        maximum_bytes=MAX_STATE_BYTES,
        label="next_state",
    )
    replay = CanonicalPayload.from_wire(
        result["replay_material"],
        maximum_bytes=MAX_REPLAY_BYTES,
        label="replay_material",
    )

    outcome_value = result["outcome_material"]
    world_outcome_hash_value = result["world_outcome_hash"]
    if outcome_value is None:
        if world_outcome_hash_value is not None:
            raise TransitionContractError(
                "World outcome hash present without outcome material"
            )
        outcome_hash: str | None = None
    else:
        outcome = CanonicalPayload.from_wire(
            outcome_value,
            maximum_bytes=MAX_OUTCOME_BYTES,
            label="outcome_material",
        )
        outcome_hash = _require_hex64(
            world_outcome_hash_value, "world_outcome_hash"
        )
        expected_outcome_hash = _outcome_hash(
            prepared.context.ruleset_revision,
            prepared.context.content_revision,
            outcome,
        )
        if outcome_hash != expected_outcome_hash:
            raise TransitionContractError("World outcome hash mismatch")

    world_transition_hash = _require_hex64(
        result["world_transition_hash"], "world_transition_hash"
    )
    facts = _accepted_facts(result)
    expected_transition_hash = domain_hash(
        TRANSITION_HASH_DOMAIN, canonical_dumps(facts)
    )
    if world_transition_hash != expected_transition_hash:
        raise TransitionContractError("World transition hash mismatch")

    _reject_authority_keys(result["next_state"]["canonical_json"], "next_state")
    _reject_authority_keys(
        result["replay_material"]["canonical_json"], "replay_material"
    )
    if outcome_value is not None:
        _reject_authority_keys(
            outcome_value["canonical_json"], "outcome_material"
        )

    return VerifiedWorldTransition(
        context=prepared.context,
        authority_context_fingerprint=prepared.context.fingerprint(),
        request_hash=request_hash,
        transition_id=prepared.transition_id,
        disposition="accepted",
        next_tick=next_tick,
        previous_state_hash=previous_state_hash,
        next_state_hash=next_state.sha256,
        replay_hash=replay.sha256,
        world_outcome_hash=outcome_hash,
        world_transition_hash=world_transition_hash,
        error_code=None,
        retryable=None,
        canonical_result=canonical_result,
        canonical_result_sha256=canonical_result_sha256,
    )


def _verify_rejected(
    prepared: PreparedWorldTransition,
    result: dict[str, Any],
    canonical_result: str,
    canonical_result_sha256: str,
) -> VerifiedWorldTransition:
    _require_exact_fields(result, REJECTED_FIELDS, "rejected World result")
    _verify_identity(prepared, result)
    code = result["code"]
    if not isinstance(code, str) or code not in STABLE_ERROR_CODES:
        raise TransitionContractError("World rejection code is not stable")
    retryable = result["retryable"]
    if not isinstance(retryable, bool):
        raise TransitionContractError("World rejection retryable must be boolean")
    if retryable != (code == "internal_unavailable"):
        raise TransitionContractError(
            "World rejection retryable disagrees with the stable error catalogue"
        )
    request_hash = _require_hex64(result["request_hash"], "request_hash")
    if request_hash != prepared.request_hash:
        raise TransitionContractError("World rejection request_hash mismatch")
    detail = result["detail"]
    if (
        not isinstance(detail, str)
        or not 1 <= len(detail) <= 256
        or any(ord(character) < 32 or ord(character) == 127 for character in detail)
    ):
        raise TransitionContractError("World rejection detail is not bounded text")

    return VerifiedWorldTransition(
        context=prepared.context,
        authority_context_fingerprint=prepared.context.fingerprint(),
        request_hash=request_hash,
        transition_id=prepared.transition_id,
        disposition="rejected",
        next_tick=None,
        previous_state_hash=None,
        next_state_hash=None,
        replay_hash=None,
        world_outcome_hash=None,
        world_transition_hash=None,
        error_code=code,
        retryable=retryable,
        canonical_result=canonical_result,
        canonical_result_sha256=canonical_result_sha256,
    )
