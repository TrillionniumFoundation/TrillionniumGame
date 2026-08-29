"""Shadow observation and fail-closed comparison for World transition v1."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .canonical import canonical_dumps
from .contracts import (
    TransitionContractError,
    VerifiedWorldTransition,
    _require_hex64,
    sha256_hex,
)

OBSERVATION_VERSION = "trnm_nakama_world_transition_observation_v1"
REPORT_VERSION = "trnm_nakama_world_transition_shadow_report_v1"
IMPLEMENTATION_REVISION = re.compile(r"^[0-9a-f]{40}$")

DETERMINISTIC_FIELDS = (
    "authority_context_fingerprint",
    "request_hash",
    "disposition",
    "next_tick",
    "previous_state_hash",
    "next_state_hash",
    "replay_hash",
    "world_outcome_hash",
    "world_transition_hash",
    "error_code",
    "retryable",
    "canonical_result_sha256",
)

OBSERVATION_FIELDS = frozenset(
    {
        "authority_context_fingerprint",
        "canonical_result_sha256",
        "contract_version",
        "disposition",
        "duration_micros",
        "error_code",
        "fixture_id",
        "implementation_id",
        "implementation_revision",
        "next_state_hash",
        "next_tick",
        "previous_state_hash",
        "replay_hash",
        "request_hash",
        "retryable",
        "world_outcome_hash",
        "world_transition_hash",
    }
)


@dataclass(frozen=True)
class ShadowObservation:
    fixture_id: str
    implementation_id: str
    implementation_revision: str
    authority_context_fingerprint: str
    request_hash: str
    disposition: str
    next_tick: int | None
    previous_state_hash: str | None
    next_state_hash: str | None
    replay_hash: str | None
    world_outcome_hash: str | None
    world_transition_hash: str | None
    error_code: str | None
    retryable: bool | None
    canonical_result_sha256: str
    duration_micros: int

    @classmethod
    def from_verified(
        cls,
        verified: VerifiedWorldTransition,
        *,
        fixture_id: str,
        implementation_id: str,
        implementation_revision: str,
        duration_micros: int,
    ) -> "ShadowObservation":
        if not fixture_id or len(fixture_id) > 160:
            raise TransitionContractError("fixture_id is missing or too long")
        if not implementation_id or len(implementation_id) > 160:
            raise TransitionContractError(
                "implementation_id is missing or too long"
            )
        if IMPLEMENTATION_REVISION.fullmatch(implementation_revision) is None:
            raise TransitionContractError(
                "implementation_revision must be exact 40-hex"
            )
        if (
            not isinstance(duration_micros, int)
            or isinstance(duration_micros, bool)
            or duration_micros < 0
        ):
            raise TransitionContractError(
                "duration_micros must be a non-negative integer"
            )
        return cls(
            fixture_id=fixture_id,
            implementation_id=implementation_id,
            implementation_revision=implementation_revision,
            authority_context_fingerprint=verified.authority_context_fingerprint,
            request_hash=verified.request_hash,
            disposition=verified.disposition,
            next_tick=verified.next_tick,
            previous_state_hash=verified.previous_state_hash,
            next_state_hash=verified.next_state_hash,
            replay_hash=verified.replay_hash,
            world_outcome_hash=verified.world_outcome_hash,
            world_transition_hash=verified.world_transition_hash,
            error_code=verified.error_code,
            retryable=verified.retryable,
            canonical_result_sha256=verified.canonical_result_sha256,
            duration_micros=duration_micros,
        )

    @classmethod
    def from_wire(cls, value: Any) -> "ShadowObservation":
        if not isinstance(value, dict) or frozenset(value) != OBSERVATION_FIELDS:
            raise TransitionContractError(
                "shadow observation has an unknown or missing field"
            )
        if value["contract_version"] != OBSERVATION_VERSION:
            raise TransitionContractError(
                "shadow observation contract version mismatch"
            )
        observation = cls(
            fixture_id=value["fixture_id"],
            implementation_id=value["implementation_id"],
            implementation_revision=value["implementation_revision"],
            authority_context_fingerprint=value[
                "authority_context_fingerprint"
            ],
            request_hash=value["request_hash"],
            disposition=value["disposition"],
            next_tick=value["next_tick"],
            previous_state_hash=value["previous_state_hash"],
            next_state_hash=value["next_state_hash"],
            replay_hash=value["replay_hash"],
            world_outcome_hash=value["world_outcome_hash"],
            world_transition_hash=value["world_transition_hash"],
            error_code=value["error_code"],
            retryable=value["retryable"],
            canonical_result_sha256=value["canonical_result_sha256"],
            duration_micros=value["duration_micros"],
        )
        observation.validate()
        return observation

    def validate(self) -> None:
        if not self.fixture_id or len(self.fixture_id) > 160:
            raise TransitionContractError("invalid shadow fixture_id")
        if not self.implementation_id or len(self.implementation_id) > 160:
            raise TransitionContractError("invalid shadow implementation_id")
        if IMPLEMENTATION_REVISION.fullmatch(self.implementation_revision) is None:
            raise TransitionContractError(
                "invalid shadow implementation_revision"
            )
        _require_hex64(
            self.authority_context_fingerprint,
            "authority_context_fingerprint",
        )
        _require_hex64(self.request_hash, "request_hash")
        _require_hex64(
            self.canonical_result_sha256, "canonical_result_sha256"
        )
        if (
            not isinstance(self.duration_micros, int)
            or isinstance(self.duration_micros, bool)
            or self.duration_micros < 0
        ):
            raise TransitionContractError("invalid shadow duration")
        if self.disposition == "accepted":
            if self.next_tick is None:
                raise TransitionContractError(
                    "accepted observation has no next_tick"
                )
            for label, value in (
                ("previous_state_hash", self.previous_state_hash),
                ("next_state_hash", self.next_state_hash),
                ("replay_hash", self.replay_hash),
                ("world_transition_hash", self.world_transition_hash),
            ):
                _require_hex64(value, label)
            if self.world_outcome_hash is not None:
                _require_hex64(
                    self.world_outcome_hash, "world_outcome_hash"
                )
            if self.error_code is not None or self.retryable is not None:
                raise TransitionContractError(
                    "accepted observation contains rejection fields"
                )
        elif self.disposition == "rejected":
            if (
                self.next_tick is not None
                or self.previous_state_hash is not None
                or self.next_state_hash is not None
                or self.replay_hash is not None
                or self.world_outcome_hash is not None
                or self.world_transition_hash is not None
            ):
                raise TransitionContractError(
                    "rejected observation contains accepted material"
                )
            if not isinstance(self.error_code, str):
                raise TransitionContractError(
                    "rejected observation lacks error_code"
                )
            if not isinstance(self.retryable, bool):
                raise TransitionContractError(
                    "rejected observation lacks retryable"
                )
        else:
            raise TransitionContractError(
                "shadow disposition must be accepted or rejected"
            )

    def to_wire(self) -> dict[str, Any]:
        self.validate()
        return {
            "authority_context_fingerprint": self.authority_context_fingerprint,
            "canonical_result_sha256": self.canonical_result_sha256,
            "contract_version": OBSERVATION_VERSION,
            "disposition": self.disposition,
            "duration_micros": self.duration_micros,
            "error_code": self.error_code,
            "fixture_id": self.fixture_id,
            "implementation_id": self.implementation_id,
            "implementation_revision": self.implementation_revision,
            "next_state_hash": self.next_state_hash,
            "next_tick": self.next_tick,
            "previous_state_hash": self.previous_state_hash,
            "replay_hash": self.replay_hash,
            "request_hash": self.request_hash,
            "retryable": self.retryable,
            "world_outcome_hash": self.world_outcome_hash,
            "world_transition_hash": self.world_transition_hash,
        }

    def canonical_json(self) -> str:
        return canonical_dumps(self.to_wire())


def _divergence_code(field: str) -> str:
    return {
        "authority_context_fingerprint": "authority_context_mismatch",
        "request_hash": "request_mismatch",
        "disposition": "disposition_mismatch",
        "next_tick": "tick_mismatch",
        "previous_state_hash": "previous_state_mismatch",
        "next_state_hash": "state_hash_mismatch",
        "replay_hash": "replay_hash_mismatch",
        "world_outcome_hash": "outcome_hash_mismatch",
        "world_transition_hash": "transition_hash_mismatch",
        "error_code": "error_code_mismatch",
        "retryable": "retryability_mismatch",
        "canonical_result_sha256": "canonical_result_mismatch",
    }[field]


def compare_observations(
    world: ShadowObservation, candidate: ShadowObservation
) -> dict[str, Any]:
    world.validate()
    candidate.validate()
    divergences: list[dict[str, Any]] = []

    if world.fixture_id != candidate.fixture_id:
        divergences.append(
            {
                "code": "fixture_mismatch",
                "field": "fixture_id",
                "world": world.fixture_id,
                "candidate": candidate.fixture_id,
            }
        )
    for field in DETERMINISTIC_FIELDS:
        left = getattr(world, field)
        right = getattr(candidate, field)
        if left != right:
            divergences.append(
                {
                    "code": _divergence_code(field),
                    "field": field,
                    "world": left,
                    "candidate": right,
                }
            )

    status = "matched" if not divergences else "diverged"
    return {
        "canonical_completion_signing_performed": False,
        "contract_version": REPORT_VERSION,
        "cutover_authorized": False,
        "divergences": divergences,
        "fixture_id": world.fixture_id,
        "promotion_eligible_for_integration_review": not divergences,
        "public_online_enabled": False,
        "status": status,
        "world_implementation": {
            "id": world.implementation_id,
            "revision": world.implementation_revision,
        },
        "candidate_implementation": {
            "id": candidate.implementation_id,
            "revision": candidate.implementation_revision,
        },
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.rstrip("\n")
            if not line:
                raise TransitionContractError(
                    f"{path}:{line_number}: blank JSONL record"
                )
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise TransitionContractError(
                    f"{path}:{line_number}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise TransitionContractError(
                    f"{path}:{line_number}: record must be an object"
                )
            records.append(value)
    return records


def compare_jsonl(
    world_path: Path, candidate_path: Path
) -> dict[str, Any]:
    world_records = [
        ShadowObservation.from_wire(record) for record in _load_jsonl(world_path)
    ]
    candidate_records = [
        ShadowObservation.from_wire(record)
        for record in _load_jsonl(candidate_path)
    ]

    def index(records: Iterable[ShadowObservation], label: str) -> dict[str, ShadowObservation]:
        result: dict[str, ShadowObservation] = {}
        for record in records:
            if record.fixture_id in result:
                raise TransitionContractError(
                    f"duplicate {label} fixture_id: {record.fixture_id}"
                )
            result[record.fixture_id] = record
        return result

    world_index = index(world_records, "world")
    candidate_index = index(candidate_records, "candidate")
    all_ids = sorted(set(world_index) | set(candidate_index))
    reports: list[dict[str, Any]] = []
    for fixture_id in all_ids:
        if fixture_id not in world_index:
            reports.append(
                {
                    "contract_version": REPORT_VERSION,
                    "cutover_authorized": False,
                    "divergences": [
                        {
                            "code": "unexpected_candidate_fixture",
                            "field": "fixture_id",
                            "world": None,
                            "candidate": fixture_id,
                        }
                    ],
                    "fixture_id": fixture_id,
                    "promotion_eligible_for_integration_review": False,
                    "public_online_enabled": False,
                    "status": "diverged",
                    "canonical_completion_signing_performed": False,
                }
            )
        elif fixture_id not in candidate_index:
            reports.append(
                {
                    "contract_version": REPORT_VERSION,
                    "cutover_authorized": False,
                    "divergences": [
                        {
                            "code": "missing_candidate_fixture",
                            "field": "fixture_id",
                            "world": fixture_id,
                            "candidate": None,
                        }
                    ],
                    "fixture_id": fixture_id,
                    "promotion_eligible_for_integration_review": False,
                    "public_online_enabled": False,
                    "status": "diverged",
                    "canonical_completion_signing_performed": False,
                }
            )
        else:
            reports.append(
                compare_observations(
                    world_index[fixture_id], candidate_index[fixture_id]
                )
            )

    divergence_count = sum(
        len(report["divergences"]) for report in reports
    )
    summary = {
        "candidate_input_sha256": sha256_hex(candidate_path.read_bytes()),
        "canonical_completion_signing_performed": False,
        "contract_version": "trnm_nakama_world_transition_shadow_summary_v1",
        "cutover_authorized": False,
        "divergence_count": divergence_count,
        "fixture_count": len(all_ids),
        "matched_count": sum(report["status"] == "matched" for report in reports),
        "promotion_eligible_for_integration_review": divergence_count == 0,
        "public_online_enabled": False,
        "reports": reports,
        "status": "matched" if divergence_count == 0 else "diverged",
        "world_input_sha256": sha256_hex(world_path.read_bytes()),
    }
    return summary
